"""审批规则配置接口：按单据类型与金额区间定义多级审批，支持会签/或签与委托代理。"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import require_perm
from ..models import ApprovalDelegation, ApprovalRule, User
from ..schemas import ApprovalDelegationIn, ApprovalRuleIn, ApprovalSlaIn
from ..services import approval_flow, approval_sla, approval_templates

router = APIRouter(prefix="/api/approval-rules", tags=["审批规则"])

DOC_TYPES = (
    "sales",
    "purchase",
    "outbound",
    "sales_return",
    "purchase_return",
    "payment",
    "supplier_payment",
    "requisition",
    "stocktake",
)


def _serialize(rule: ApprovalRule) -> dict:
    return {
        "id": rule.id,
        "doc_type": rule.doc_type,
        "name": rule.name,
        "enabled": bool(rule.enabled),
        "min_amount": rule.min_amount,
        "max_amount": rule.max_amount,
        "level": rule.level,
        "permission": rule.permission,
        "level_name": rule.level_name,
        "mode": rule.mode or "single",
        "approvers": rule.approvers or "",
        "conditions": rule.conditions or "",
        "created_by": rule.created_by,
        "updated_at": rule.updated_at,
    }


def _validate(payload: ApprovalRuleIn) -> None:
    if payload.doc_type not in DOC_TYPES:
        raise HTTPException(status_code=400, detail="单据类型不支持")
    if payload.level < 1:
        raise HTTPException(status_code=400, detail="审批级别从 1 开始")
    if payload.min_amount < 0:
        raise HTTPException(status_code=400, detail="金额下限不能为负数")
    if payload.max_amount is not None and payload.max_amount <= payload.min_amount:
        raise HTTPException(status_code=400, detail="金额上限必须大于下限")
    if payload.mode not in approval_flow.VALID_MODES:
        raise HTTPException(status_code=400, detail="审批模式只能是 single/or/counter")
    if payload.mode in ("or", "counter") and not payload.approvers.strip():
        raise HTTPException(status_code=400, detail="或签/会签模式下必须指定审批人名单")


@router.get("", summary="审批规则列表")
def list_rules(
    doc_type: str = Query(""),
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("approval:manage")),
):
    stmt = select(ApprovalRule).order_by(ApprovalRule.doc_type, ApprovalRule.level, ApprovalRule.id)
    if doc_type:
        stmt = stmt.where(ApprovalRule.doc_type == doc_type)
    return [_serialize(row) for row in db.scalars(stmt).all()]


@router.get("/options", summary="审批配置下拉选项（单据类型/仓库/权限）")
def config_options(
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("approval:manage")),
):
    """条件分支要用到仓库与权限清单，集中返回，避免前端跨页取数踩权限。"""
    from ..models import Product, Warehouse
    from ..permissions import ALL_PERMISSIONS

    categories = sorted(
        {row[0].strip() for row in db.query(Product.category).distinct().all() if row[0] and row[0].strip()}
    )
    return {
        "doc_types": [
            {"value": key, "label": approval_flow.DOC_LABELS.get(key, key)} for key in DOC_TYPES
        ],
        "warehouses": [
            {"id": w.id, "name": w.name}
            for w in db.query(Warehouse).order_by(Warehouse.id).all()
        ],
        "product_categories": categories,
        "permissions": [{"value": p, "label": p} for p in ALL_PERMISSIONS],
    }


@router.get("/templates", summary="行业审批模板清单")
def list_templates(
    _: User = Depends(require_perm("approval:manage")),
):
    return [
        {
            "key": item["key"],
            "name": item["name"],
            "desc": item["desc"],
            "doc_types": item["doc_types"],
            "rule_count": len(item["rules"]),
        }
        for item in approval_templates.TEMPLATES
    ]


@router.post("/templates/{key}/apply", summary="一键套用行业审批模板")
def apply_template(
    key: str,
    request: Request,
    replace: bool = Query(True, description="是否先清空这些单据类型的旧规则"),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("approval:manage")),
):
    if approval_templates.get_template(key) is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    result = approval_templates.apply_template(db, key, replace=replace)
    record(
        username=user.username,
        role=user.role,
        action="套用审批模板",
        resource=f"审批模板 {result['name']}",
        detail=f"新增 {result['created']} 条规则，清理 {result['removed']} 条旧规则",
        request=request,
    )
    return result


@router.get("/sla", summary="审批超时规则")
def list_sla(
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("approval:manage")),
):
    return {"items": approval_sla.list_configs(db)}


@router.put("/sla/{doc_type}", summary="保存审批超时规则")
def save_sla(
    doc_type: str,
    payload: ApprovalSlaIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("approval:manage")),
):
    try:
        result = approval_sla.save_config(
            db,
            doc_type,
            remind_hours=payload.remind_hours,
            escalate_hours=payload.escalate_hours,
            enabled=payload.enabled,
            username=user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record(
        username=user.username,
        role=user.role,
        action="保存审批超时规则",
        resource=f"单据类型 {result['label']}",
        detail=(
            f"提醒 {result['remind_hours']} 小时"
            + (f"，升级 {result['escalate_hours']} 小时" if result["escalate_hours"] else "")
            + f"，{'启用' if result['enabled'] else '停用'}"
        ),
        request=request,
    )
    return result


@router.post("/sla/scan", summary="立即执行审批超时巡检")
def scan_sla(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("approval:manage")),
):
    result = approval_sla.scan(db, force=True)
    record(
        username=user.username,
        role=user.role,
        action="手动审批超时巡检",
        resource="审批超时规则",
        detail=f"待审批 {result['checked']} 条，提醒 {result['reminded']} 条，升级 {result['escalated']} 条",
        request=request,
    )
    return result


@router.get("/preview", summary="按金额/条件预览审批链")
def preview(
    doc_type: str = Query("sales"),
    amount: float = Query(0, ge=0),
    customer_level: str = Query("", description="可选：客户等级 A/B/C"),
    warehouse_id: int | None = Query(None, description="可选：仓库 id"),
    created_by: str = Query("", description="可选：制单人用户名"),
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("approval:manage")),
):
    if doc_type not in DOC_TYPES:
        raise HTTPException(status_code=400, detail="单据类型不支持")
    ctx: dict = {}
    if customer_level:
        ctx["customer_level"] = customer_level
    if warehouse_id:
        ctx["warehouse_id"] = int(warehouse_id)
    if created_by:
        ctx["created_by"] = created_by
    return approval_flow.describe(db, doc_type, amount, 0, ctx=ctx or None)


@router.post("", summary="新建审批规则", dependencies=[Depends(require_perm("approval:manage"))])
def create_rule(
    payload: ApprovalRuleIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("approval:manage")),
):
    _validate(payload)
    rule = ApprovalRule(
        doc_type=payload.doc_type,
        name=payload.name.strip(),
        enabled=payload.enabled,
        min_amount=payload.min_amount,
        max_amount=payload.max_amount,
        level=payload.level,
        permission=payload.permission.strip(),
        level_name=payload.level_name.strip(),
        mode=payload.mode,
        approvers=",".join(
            a.strip() for a in payload.approvers.split(",") if a.strip()
        ),
        conditions=json.dumps(payload.conditions or {}, ensure_ascii=False),
        created_by=user.username,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    record(
        username=user.username,
        role=user.role,
        action="新建审批规则",
        resource=f"审批规则 {rule.name or rule.id}",
        detail=f"{rule.doc_type} 第 {rule.level} 级，金额 {rule.min_amount}~{rule.max_amount or '不限'}",
        request=request,
    )
    return _serialize(rule)


@router.put("/{rule_id}", summary="修改审批规则")
def update_rule(
    rule_id: int,
    payload: ApprovalRuleIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("approval:manage")),
):
    _validate(payload)
    rule = db.get(ApprovalRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="审批规则不存在")
    rule.doc_type = payload.doc_type
    rule.name = payload.name.strip()
    rule.enabled = payload.enabled
    rule.min_amount = payload.min_amount
    rule.max_amount = payload.max_amount
    rule.level = payload.level
    rule.permission = payload.permission.strip()
    rule.level_name = payload.level_name.strip()
    rule.mode = payload.mode
    rule.approvers = ",".join(
        a.strip() for a in payload.approvers.split(",") if a.strip()
    )
    rule.conditions = json.dumps(payload.conditions or {}, ensure_ascii=False)
    db.commit()
    db.refresh(rule)
    record(
        username=user.username,
        role=user.role,
        action="修改审批规则",
        resource=f"审批规则 {rule.name or rule.id}",
        detail=f"{rule.doc_type} 第 {rule.level} 级",
        request=request,
    )
    return _serialize(rule)


@router.delete("/{rule_id}", summary="删除审批规则", dependencies=[Depends(require_perm("approval:manage"))])
def delete_rule(
    rule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("approval:manage")),
):
    rule = db.get(ApprovalRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="审批规则不存在")
    name = rule.name or str(rule.id)
    db.delete(rule)
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="删除审批规则",
        resource=f"审批规则 {name}",
        detail="删除规则后按剩余规则重新计算审批级数",
        request=request,
    )
    return {"ok": True}


@router.post("/seed-example", summary="生成示例审批规则（仅当没有任何规则时）")
def seed_example(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("approval:manage")),
):
    before = db.scalar(select(ApprovalRule.id).limit(1))
    approval_flow.seed_default_rules(db)
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="生成示例审批规则",
        resource="审批规则",
        detail="万元以下单级、以上两级",
        request=request,
    )
    return {"ok": True, "created": before is None}


# ---------------- 委托代理 ----------------
def _serialize_delegation(d: ApprovalDelegation) -> dict:
    return {
        "id": d.id,
        "doc_type": d.doc_type,
        "delegator": d.delegator,
        "delegatee": d.delegatee,
        "enabled": bool(d.enabled),
        "created_by": d.created_by,
        "created_at": d.created_at,
        "updated_at": d.updated_at,
    }


@router.get("/delegations", summary="委托代理列表")
def list_delegations(
    doc_type: str = Query(""),
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("approval:manage")),
):
    stmt = select(ApprovalDelegation).order_by(ApprovalDelegation.id.desc())
    if doc_type:
        stmt = stmt.where(ApprovalDelegation.doc_type == doc_type)
    return [_serialize_delegation(row) for row in db.scalars(stmt).all()]


@router.post("/delegations", summary="新增委托代理", dependencies=[Depends(require_perm("approval:manage"))])
def create_delegation(
    payload: ApprovalDelegationIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("approval:manage")),
):
    delegator = payload.delegator.strip()
    delegatee = payload.delegatee.strip()
    if payload.doc_type not in DOC_TYPES:
        raise HTTPException(status_code=400, detail="单据类型不支持")
    if not delegator or not delegatee:
        raise HTTPException(status_code=400, detail="委托人与代理人不能为空")
    if delegator == delegatee:
        raise HTTPException(status_code=400, detail="委托人不能代理给自己")
    # 校验用户存在
    names = {delegator, delegatee}
    found = {
        row.username
        for row in db.scalars(select(User).where(User.username.in_(names))).all()
    }
    missing = names - found
    if missing:
        raise HTTPException(status_code=400, detail=f"用户不存在：{'、'.join(sorted(missing))}")
    delegation = ApprovalDelegation(
        doc_type=payload.doc_type,
        delegator=delegator,
        delegatee=delegatee,
        enabled=payload.enabled,
        created_by=user.username,
    )
    db.add(delegation)
    db.commit()
    db.refresh(delegation)
    record(
        username=user.username,
        role=user.role,
        action="新增审批委托",
        resource=f"委托 {delegator} → {delegatee}",
        detail=f"{payload.doc_type} 类型审批委托",
        request=request,
    )
    return _serialize_delegation(delegation)


@router.put("/delegations/{delegation_id}", summary="修改委托代理", dependencies=[Depends(require_perm("approval:manage"))])
def update_delegation(
    delegation_id: int,
    payload: ApprovalDelegationIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("approval:manage")),
):
    delegation = db.get(ApprovalDelegation, delegation_id)
    if not delegation:
        raise HTTPException(status_code=404, detail="委托关系不存在")
    if payload.doc_type not in DOC_TYPES:
        raise HTTPException(status_code=400, detail="单据类型不支持")
    delegation.doc_type = payload.doc_type
    delegation.enabled = payload.enabled
    db.commit()
    db.refresh(delegation)
    record(
        username=user.username,
        role=user.role,
        action="修改审批委托",
        resource=f"委托 {delegation.delegator} → {delegation.delegatee}",
        detail=f"启用={delegation.enabled}",
        request=request,
    )
    return _serialize_delegation(delegation)


@router.delete("/delegations/{delegation_id}", summary="删除委托代理", dependencies=[Depends(require_perm("approval:manage"))])
def delete_delegation(
    delegation_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("approval:manage")),
):
    delegation = db.get(ApprovalDelegation, delegation_id)
    if not delegation:
        raise HTTPException(status_code=404, detail="委托关系不存在")
    db.delete(delegation)
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="删除审批委托",
        resource=f"委托 {delegation.delegator} → {delegation.delegatee}",
        detail="删除委托代理关系",
        request=request,
    )
    return {"ok": True}
