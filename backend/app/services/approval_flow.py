"""多级审批：按金额区间匹配规则，逐级校验权限。

规则按「单据类型 + 金额区间 + 级别」配置；命中几条就走几级审批。
没有配置任何规则时退回单级审批，保证升级后老账套行为不变。

每级审批支持三种模式：
- single ：单人审批（默认），按权限 key 校验，任何人持权限即可通过；
- or     ：或签，指定审批人名单中「任一」通过即算本级通过；
- counter：会签，指定审批人名单中「全部」通过才算本级通过。

同时支持委托代理：delegatee 可代表 delegator（委托人）审批对应单据类型，
权限模式下按委托人的角色判断，名单模式下按委托人是否在名单中判断。
"""
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ApprovalDelegation, ApprovalLog, ApprovalRule, Customer, User
from ..permissions import has_permission
from . import approvals

DOC_LABELS = {
    "sales": "销售订单",
    "purchase": "采购订单",
    "outbound": "销售出库单",
    "sales_return": "销售退货单",
    "purchase_return": "采购退货单",
    "payment": "收款单",
    "supplier_payment": "付款单",
    "requisition": "采购申请",
    "stocktake": "盘点单",
}
DEFAULT_PERMISSIONS = {
    "sales": "sales:approve",
    "purchase": "purchase:approve",
    "outbound": "outbound:approve",
    "sales_return": "return:approve",
    "purchase_return": "return:approve",
    "payment": "payable:approve",
    "supplier_payment": "payable:approve",
    "requisition": "requisition:approve",
    "stocktake": "stocktake:approve",
}
VALID_MODES = ("single", "or", "counter")


def _conditions_of(rule: ApprovalRule) -> dict:
    """解析规则的 JSON 条件分支；解析失败按无条件处理。"""
    import json

    if not rule.conditions:
        return {}
    try:
        data = json.loads(rule.conditions)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def evaluate_conditions(rule: ApprovalRule, ctx: dict | None) -> bool:
    """条件分支：规则 conditions 为空则恒命中；否则按 AND 语义逐项校验。

    支持的条件键（可组合，全部满足才命中）：
    credit_exceeded / customer_level / min_margin / max_margin /
    warehouse_id / created_by / doc_type
    """
    conditions = _conditions_of(rule)
    if not conditions:
        return True
    ctx = ctx or {}
    for key, expected in conditions.items():
        if key == "credit_exceeded":
            if bool(ctx.get("credit_exceeded")) != bool(expected):
                return False
        elif key == "customer_level":
            levels = expected if isinstance(expected, list) else [expected]
            if ctx.get("customer_level") not in levels:
                return False
        elif key == "min_margin":
            margin = ctx.get("margin")
            if margin is None or float(margin) >= float(expected):
                return False
        elif key == "max_margin":
            margin = ctx.get("margin")
            if margin is not None and float(margin) > float(expected):
                return False
        elif key in ("warehouse_id", "warehouse_ids"):
            allowed = expected if isinstance(expected, list) else [expected]
            try:
                allowed = {int(v) for v in allowed}
            except (TypeError, ValueError):
                return False
            if ctx.get("warehouse_id") is None or int(ctx["warehouse_id"]) not in allowed:
                return False
        elif key in ("created_by", "creators"):
            allowed = expected if isinstance(expected, list) else [expected]
            names = {str(v).strip() for v in allowed if str(v).strip()}
            if str(ctx.get("created_by") or "") not in names:
                return False
        elif key in ("product_category", "product_categories"):
            allowed = expected if isinstance(expected, list) else [expected]
            categories = {str(v) for v in allowed if str(v).strip()}
            actual = set(ctx.get("product_categories") or [])
            if not categories or not (categories & actual):
                return False
        # 未知条件键忽略，保证向前兼容
    return True


def has_rules(db: Session, doc_type: str) -> bool:
    """该单据类型是否配置了启用中的审批规则；没有规则时沿用各自的旧流程。"""
    return (
        db.scalar(
            select(ApprovalRule.id)
            .where(ApprovalRule.doc_type == doc_type)
            .where(ApprovalRule.enabled == True)  # noqa: E712
            .limit(1)
        )
        is not None
    )


def _amount_of(order) -> float:
    """取单据金额：订单用 total_amount，收付款单用 amount，盘点单没有金额按 0。"""
    for field in ("total_amount", "amount"):
        value = getattr(order, field, None)
        if value is not None:
            return float(value)
    return 0.0


def context_for(db: Session, doc_type: str, order) -> dict:
    """按单据类型算出条件分支所需的上下文（金额之外的全部维度）。"""
    ctx: dict = {}
    if order is None:
        return ctx
    # 通用维度：制单人与仓库（订单没有仓库，出库/退货/盘点有）
    ctx["created_by"] = getattr(order, "created_by", "") or ""
    warehouse_id = getattr(order, "warehouse_id", None)
    if warehouse_id:
        ctx["warehouse_id"] = int(warehouse_id)

    # 商品分类维度：明细里出现过的分类集合（用于「按分类」触发审批）
    items = getattr(order, "items", None) or []
    categories = {
        str(item.product.category or "").strip()
        for item in items
        if getattr(item, "product", None) is not None
    }
    if categories:
        ctx["product_categories"] = sorted(c for c in categories if c)

    if doc_type == "sales":
        customer = db.get(Customer, order.customer_id) if getattr(order, "customer_id", None) else None
        if customer is not None:
            ctx["customer_level"] = customer.level or "C"
            if customer.credit_limit and customer.credit_limit > 0:
                from . import credit

                exposure = credit.exposure(db, customer.id)
                projected = round(float(exposure["outstanding"]) + float(order.total_amount or 0), 2)
                ctx["credit_exceeded"] = projected > float(customer.credit_limit)
        total = float(getattr(order, "total_amount", 0) or 0)
        if total > 0:
            cost = 0.0
            items = getattr(order, "items", None)
            if items:
                cost = sum(
                    float((item.product.avg_cost or item.product.cost_price) if item.product else 0)
                    * float(item.quantity or 0)
                    for item in items
                )
            ctx["margin"] = round((total - cost) / total, 4) if total else 0.0
    return ctx


def matching_rules(db: Session, doc_type: str, amount: float, ctx: dict | None = None) -> list[ApprovalRule]:
    """命中金额区间与条件分支的启用规则，按级别升序。金额区间为 [min, max)。"""
    rows = db.scalars(
        select(ApprovalRule)
        .where(ApprovalRule.doc_type == doc_type)
        .where(ApprovalRule.enabled == True)  # noqa: E712
        .order_by(ApprovalRule.level, ApprovalRule.id)
    ).all()
    value = float(amount or 0)
    return [
        rule
        for rule in rows
        if value >= float(rule.min_amount or 0)
        and (rule.max_amount is None or value < float(rule.max_amount))
        and evaluate_conditions(rule, ctx)
    ]


def levels_for(db: Session, doc_type: str, amount: float, ctx: dict | None = None) -> list[ApprovalRule]:
    """每一级取第一条命中的规则，按 level 排序。"""
    picked: dict[int, ApprovalRule] = {}
    for rule in matching_rules(db, doc_type, amount, ctx):
        picked.setdefault(int(rule.level or 1), rule)
    return [picked[level] for level in sorted(picked)]


def required_levels(db: Session, doc_type: str, amount: float, ctx: dict | None = None) -> int:
    """应走几级；没有规则命中时按单级处理。"""
    return max(len(levels_for(db, doc_type, amount, ctx)), 1)


def level_rule(db: Session, doc_type: str, amount: float, level: int, ctx: dict | None = None) -> ApprovalRule | None:
    for rule in levels_for(db, doc_type, amount, ctx):
        if int(rule.level or 1) == level:
            return rule
    return None


def split_approvers(rule: ApprovalRule | None) -> list[str]:
    """指定审批人名单（逗号分隔，去空白去重）。"""
    if not rule:
        return []
    return [a.strip() for a in (rule.approvers or "").split(",") if a.strip()]


def _role_of(db: Session, username: str) -> str:
    if not username:
        return ""
    user = db.scalar(select(User).where(User.username == username))
    return user.role if user else ""


def resolve_principals(db: Session, doc_type: str, user) -> set[str]:
    """当前用户可代表的身份：本人 + 所有委派给他的委托人。"""
    principals = {user.username}
    rows = db.scalars(
        select(ApprovalDelegation).where(
            ApprovalDelegation.doc_type == doc_type,
            ApprovalDelegation.delegatee == user.username,
            ApprovalDelegation.enabled == True,  # noqa: E712
        )
    ).all()
    for delegation in rows:
        principals.add(delegation.delegator)
    return principals


def is_delegatee(db: Session, doc_type: str, username: str) -> bool:
    """该用户是否是某笔委托的代理人（用于待办列表展示）。"""
    return (
        db.scalar(
            select(ApprovalDelegation.id)
            .where(
                ApprovalDelegation.doc_type == doc_type,
                ApprovalDelegation.delegatee == username,
                ApprovalDelegation.enabled == True,  # noqa: E712
            )
            .limit(1)
        )
        is not None
    )


def _approver_principal(db: Session, doc_type: str, user, approvers: list[str]) -> str | None:
    """为指定审批人名单解析出「实际代表谁」；不在名单且无委托则返回 None。"""
    if not approvers:
        return None
    if user.username in approvers:
        return user.username
    principals = resolve_principals(db, doc_type, user)
    for approver in approvers:
        if approver in principals:
            return approver
    return None


def _has_level_access(db: Session, doc_type: str, user, rule: ApprovalRule | None, permission: str) -> bool:
    """能否审批本级：名单模式按名单/委托，权限模式按角色/委托。"""
    approvers = split_approvers(rule)
    if approvers:
        return _approver_principal(db, doc_type, user, approvers) is not None
    principals = resolve_principals(db, doc_type, user)
    for principal in principals:
        role = user.role if principal == user.username else _role_of(db, principal)
        if role and has_permission(role, permission):
            return True
    return False


def approved_principals(db: Session, doc_type: str, order_id: int, level: int) -> set[str]:
    """本级已有哪些 principal 审批通过（会签去重与进度用）。"""
    rows = db.scalars(
        select(ApprovalLog).where(
            ApprovalLog.order_type == doc_type,
            ApprovalLog.order_id == order_id,
            ApprovalLog.action == "approve",
            ApprovalLog.level == level,
        )
    ).all()
    return {row.principal or row.operator for row in rows}


def _level_label(rule: ApprovalRule | None, level: int) -> str:
    return rule.level_name if rule and rule.level_name else f"第 {level} 级"


def ensure_level_permission(db: Session, doc_type: str, order, user) -> tuple[int, ApprovalRule | None]:
    """校验当前用户能否通过「下一级」审批，返回 (级别, 规则)。"""
    level = int(order.approval_approved or 0) + 1
    ctx = context_for(db, doc_type, order)
    rule = level_rule(db, doc_type, _amount_of(order), level, ctx)
    permission = (rule.permission if rule and rule.permission else DEFAULT_PERMISSIONS.get(doc_type, ""))
    if not _has_level_access(db, doc_type, user, rule, permission):
        label = _level_label(rule, level)
        approvers = split_approvers(rule)
        detail = (
            f"当前用户不在{label}的审批人名单中"
            if approvers
            else f"当前角色无法通过{label}审批（需要权限 {permission}）"
        )
        raise HTTPException(status_code=403, detail=detail)
    return level, rule


def perform_approval(db: Session, doc_type: str, order, user, comment: str = "") -> dict:
    """执行一次「审批通过」，按模式判定本级/整单是否完成。

    返回字段：
    - level / required：当前级别与总级数
    - level_complete：本级是否算通过（会签未齐为 False）
    - completed：整单是否走完所有级别
    - principal：本次实际上代表谁（会签去重与留痕用）
    """
    level = int(order.approval_approved or 0) + 1
    required = int(order.approval_required or 1)
    ctx = context_for(db, doc_type, order)
    rule = level_rule(db, doc_type, _amount_of(order), level, ctx)
    permission = (rule.permission if rule and rule.permission else DEFAULT_PERMISSIONS.get(doc_type, ""))
    approvers = split_approvers(rule)
    mode = (rule.mode if rule and rule.mode else "single") or "single"

    if approvers:
        principal = _approver_principal(db, doc_type, user, approvers)
        if principal is None:
            raise HTTPException(
                status_code=403,
                detail=f"当前用户不在{_level_label(rule, level)}的审批人名单中",
            )
    elif not _has_level_access(db, doc_type, user, rule, permission):
        raise HTTPException(
            status_code=403,
            detail=f"当前角色无法通过{_level_label(rule, level)}审批（需要权限 {permission}）",
        )
    else:
        principal = user.username

    # 会签去重：同一 principal 不能重复批同一级
    if mode == "counter":
        done = approved_principals(db, doc_type, order.id, level)
        if principal in done:
            raise HTTPException(status_code=400, detail=f"{principal} 已在本级审批通过，无需重复操作")

    approvals.log_action(db, doc_type, order.id, "approve", user, comment, level=level, principal=principal)
    # 立即 flush，让刚写入的审批在会签去重/完成判定里可见（会话 autoflush=False）
    db.flush()

    if mode == "counter" and approvers:
        done = approved_principals(db, doc_type, order.id, level)
        level_complete = set(approvers) <= done
    else:
        level_complete = True

    if level_complete:
        order.approval_approved = level

    return {
        "level": level,
        "required": required,
        "level_complete": level_complete,
        "completed": level_complete and level >= required,
        "principal": principal,
    }


def describe(db: Session, doc_type: str, amount: float, approved: int = 0, order_id: int | None = None, ctx: dict | None = None) -> dict:
    """给前端展示的审批链：每级名称、所需权限/名单、审批模式、是否已通过。"""
    levels = levels_for(db, doc_type, amount, ctx)
    if not levels:
        return {
            "required": 1,
            "approved": int(approved or 0),
            "levels": [{
                "level": 1,
                "name": "默认审批",
                "permission": DEFAULT_PERMISSIONS.get(doc_type, ""),
                "mode": "single",
                "approvers": [],
                "passed": int(approved or 0) >= 1,
            }],
        }
    result = {
        "required": len(levels),
        "approved": int(approved or 0),
        "levels": [],
    }
    for rule in levels:
        lvl = int(rule.level or 1)
        entry = {
            "level": lvl,
            "name": rule.level_name or f"第 {lvl} 级",
            "permission": rule.permission,
            "mode": (rule.mode or "single"),
            "approvers": split_approvers(rule),
            "passed": int(approved or 0) >= lvl,
        }
        if order_id is not None:
            entry["approved_by"] = sorted(approved_principals(db, doc_type, order_id, lvl))
        result["levels"].append(entry)
    return result


def seed_default_rules(db: Session) -> None:
    """首次启用时给一张「万元以下单级、以上两级」的示例规则，避免空配置无感。

    只在完全没有规则时写入，已有配置不覆盖。
    """
    existing = db.scalar(select(ApprovalRule.id).limit(1))
    if existing:
        return
    rows = [
        ApprovalRule(
            doc_type="sales", name="常规销售审批", min_amount=0, max_amount=10000,
            level=1, permission="sales:approve", level_name="销售主管",
        ),
        ApprovalRule(
            doc_type="sales", name="大额销售审批-一级", min_amount=10000, max_amount=None,
            level=1, permission="sales:approve", level_name="销售主管",
        ),
        ApprovalRule(
            doc_type="sales", name="大额销售审批-二级", min_amount=10000, max_amount=None,
            level=2, permission="finance:manage", level_name="财务复核",
        ),
        ApprovalRule(
            doc_type="purchase", name="常规采购审批", min_amount=0, max_amount=10000,
            level=1, permission="purchase:approve", level_name="采购主管",
        ),
        ApprovalRule(
            doc_type="purchase", name="大额采购审批-一级", min_amount=10000, max_amount=None,
            level=1, permission="purchase:approve", level_name="采购主管",
        ),
        ApprovalRule(
            doc_type="purchase", name="大额采购审批-二级", min_amount=10000, max_amount=None,
            level=2, permission="finance:manage", level_name="财务复核",
        ),
    ]
    now = datetime.now()
    for row in rows:
        row.created_at = now
        row.updated_at = now
        db.add(row)
    db.flush()
