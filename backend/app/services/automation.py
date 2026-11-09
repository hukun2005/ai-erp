"""自动化规则引擎。

规则 = 触发条件 + 动作。引擎只做两件安全的事：找出符合条件的数据，
然后生成**草稿**采购单或推送通知。它不会确认订单、不会审批、不会改库存，
所以即使规则配错，最多多出几张草稿或几条消息，不会动到账实。

触发条件与动作都登记在下面的注册表里，前端据此动态渲染表单；
新增一类规则只要加一个求值函数和一个执行函数，不用改表结构。
"""
import logging
import math
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AutomationRule,
    AutomationRun,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    Supplier,
)
from ..utils import gen_order_no
from . import analytics, feishu, notify_config, receivables

logger = logging.getLogger(__name__)

MAX_HITS = 200
PURCHASE_OPEN_STATUS = ("draft", "pending", "ordered")


class AutomationError(Exception):
    """可预期的执行失败：信息会原样写进执行记录并展示给用户。"""


# ---------------- 触发条件 ----------------
def _int_config(config: dict, key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _product_hit(product: Product) -> dict:
    return {
        "type": "product",
        "id": product.id,
        "name": product.name,
        "sku": product.sku,
        "stock_qty": product.stock_qty,
        "safety_stock": product.safety_stock,
        "unit": product.unit,
        "label": (
            f"{product.sku} {product.name}（库存 {product.stock_qty}{product.unit} / "
            f"安全 {product.safety_stock}{product.unit}）"
        ),
    }


def _eval_stock_below_safety(db: Session, config: dict) -> list[dict]:
    rows = db.scalars(
        select(Product)
        .where(Product.is_active == True)  # noqa: E712
        .where(Product.stock_qty <= Product.safety_stock)
        .order_by((Product.stock_qty - Product.safety_stock), Product.id)
    ).all()
    return [_product_hit(p) for p in rows]


def _eval_stock_out(db: Session, config: dict) -> list[dict]:
    rows = db.scalars(
        select(Product)
        .where(Product.is_active == True)  # noqa: E712
        .where(Product.stock_qty <= 0)
        .order_by(Product.id)
    ).all()
    return [_product_hit(p) for p in rows]


def _eval_receivable_overdue(db: Session, config: dict) -> list[dict]:
    days = _int_config(config, "days", 30, 1, 365)
    items = []
    for row in receivables.customer_receivables(db):
        if row["age_days"] < days:
            continue
        items.append(
            {
                "type": "customer",
                "id": row["customer_id"],
                "name": row["customer"],
                "balance": row["balance"],
                "age_days": row["age_days"],
                "bucket": row["bucket"],
                "label": f"{row['customer']} 未结 {row['balance']:,.2f}，最长账龄 {row['age_days']} 天",
            }
        )
    return items


def _eval_customer_inactive(db: Session, config: dict) -> list[dict]:
    days = _int_config(config, "days", 45, 1, 365)
    items = []
    for row in analytics.customer_stats(db, 90):
        if not row["last_order"]:
            continue
        if (row["days_since_last"] or 0) < days:
            continue
        items.append(
            {
                "type": "customer",
                "id": row["id"],
                "name": row["name"],
                "level": row["level"],
                "amount": row["amount"],
                "days_since_last": row["days_since_last"],
                "label": f"{row['name']}（{row['level']} 级）已 {row['days_since_last']} 天未下单",
            }
        )
    return items


# ---------------- 动作 ----------------
def _last_supplier_map(db: Session) -> dict[int, int]:
    rows = db.execute(
        select(PurchaseOrderItem.product_id, PurchaseOrder.supplier_id)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.order_id)
        .order_by(PurchaseOrder.id.desc())
    ).all()
    result: dict[int, int] = {}
    for product_id, supplier_id in rows:
        result.setdefault(int(product_id), int(supplier_id))
    return result


def _plan_purchase(db: Session, hits: list[dict], config: dict) -> dict:
    """把命中商品折算成「按供应商分组」的补货计划，不写库。"""
    product_ids = [int(hit["id"]) for hit in hits if hit.get("type") == "product"]
    if not product_ids:
        return {"groups": {}, "skipped": ["命中的不是商品，无法生成采购单"], "items": 0}

    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(product_ids))).all()}
    covered = set(
        db.scalars(
            select(PurchaseOrderItem.product_id)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.order_id)
            .where(PurchaseOrder.status.in_(PURCHASE_OPEN_STATUS))
            .where(PurchaseOrderItem.product_id.in_(product_ids))
        ).all()
    )
    lead_time = _int_config(config, "lead_time_days", 15, 1, 180)
    sold = {row["id"]: row["qty"] for row in analytics.product_sales(db, 30)}
    last_supplier = _last_supplier_map(db)
    suppliers = {s.id: s for s in db.scalars(select(Supplier)).all()}
    by_category = {s.supply_category: s.id for s in suppliers.values() if s.supply_category}

    groups: dict[int, list[dict]] = {}
    skipped: list[str] = []
    for product_id in product_ids:
        product = products.get(product_id)
        if product is None:
            continue
        if product_id in covered:
            skipped.append(f"{product.name}（已有未完成采购单）")
            continue
        supplier_id = last_supplier.get(product_id) or by_category.get(product.category)
        if not supplier_id or supplier_id not in suppliers:
            skipped.append(f"{product.name}（未关联供应商）")
            continue
        avg_daily = sold.get(product_id, 0) / 30
        # 补到安全线之上，再备足一个补货周期的量；日均销量取近 30 天实际出库
        need = max(product.safety_stock - product.stock_qty, 0) + math.ceil(avg_daily * lead_time)
        quantity = max(int(need), 1)
        unit_price = round(float(product.cost_price or 0), 2)
        groups.setdefault(supplier_id, []).append(
            {
                "product_id": product.id,
                "name": product.name,
                "sku": product.sku,
                "quantity": quantity,
                "unit_price": unit_price,
                "amount": round(quantity * unit_price, 2),
            }
        )
    return {
        "groups": groups,
        "skipped": skipped,
        "items": sum(len(items) for items in groups.values()),
    }


def _preview_purchase(db: Session, rule: AutomationRule, hits: list[dict]) -> dict:
    plan = _plan_purchase(db, hits, rule.action_config or {})
    suppliers = {s.id: s.name for s in db.scalars(select(Supplier)).all()}
    return {
        "orders": [
            {
                "supplier_id": supplier_id,
                "supplier_name": suppliers.get(supplier_id, ""),
                "item_count": len(items),
                "amount": round(sum(item["amount"] for item in items), 2),
                "items": items,
            }
            for supplier_id, items in plan["groups"].items()
        ],
        "skipped": plan["skipped"],
    }


def _act_create_purchase_draft(
    db: Session, rule: AutomationRule, hits: list[dict], config: dict, operator: str
) -> dict:
    plan = _plan_purchase(db, hits, config)
    if not plan["groups"]:
        # 命中的商品都已在未完成的采购单里，这是正常结果，不该记成失败
        return {"created_orders": [], "skipped": plan["skipped"]}
    suppliers = {s.id: s.name for s in db.scalars(select(Supplier)).all()}
    orders = []
    for supplier_id, items in plan["groups"].items():
        order = PurchaseOrder(
            order_no=gen_order_no(db, PurchaseOrder, "PO"),
            supplier_id=supplier_id,
            status="draft",
            order_date=datetime.now(),
            total_amount=round(sum(item["amount"] for item in items), 2),
            remark=f"由自动化规则「{rule.name}」自动生成，请核对后提交",
            created_by=operator,
        )
        for item in items:
            order.items.append(
                PurchaseOrderItem(
                    product_id=item["product_id"],
                    quantity=item["quantity"],
                    unit_price=item["unit_price"],
                    amount=item["amount"],
                )
            )
        db.add(order)
        db.flush()
        orders.append(
            {
                "order_no": order.order_no,
                "supplier_name": suppliers.get(supplier_id, ""),
                "item_count": len(items),
                "amount": order.total_amount,
            }
        )
    return {"created_orders": orders, "skipped": plan["skipped"]}


def _act_notify_feishu(
    db: Session, rule: AutomationRule, hits: list[dict], config: dict, operator: str
) -> dict:
    cfg = notify_config.effective(db)
    if not notify_config.ready(cfg):
        raise AutomationError("尚未配置飞书机器人，无法推送通知")
    title = str(config.get("title") or "").strip() or f"【AI-ERP】{rule.name}"
    lines = [f"**命中对象**：{len(hits)} 个"]
    for hit in hits[:10]:
        lines.append(f"- {hit.get('label') or hit.get('name')}")
    if len(hits) > 10:
        lines.append(f"- 其余 {len(hits) - 10} 个略")
    card = feishu.build_card(
        title=title,
        template="orange",
        lines=lines,
        footer=f"自动化规则「{rule.name}」 · {datetime.now():%Y-%m-%d %H:%M}",
    )
    feishu.send_card(card, cfg["webhook_url"], cfg["secret"])
    return {"sent": True, "count": len(hits), "title": title}


# ---------------- 注册表 ----------------
TRIGGERS: dict[str, dict] = {
    "stock_below_safety": {
        "label": "库存低于安全线",
        "description": "在售商品的当前库存 ≤ 安全库存，按缺口从大到小排序",
        "fields": [],
        "evaluate": _eval_stock_below_safety,
    },
    "stock_out": {
        "label": "库存已售罄",
        "description": "在售商品当前库存 ≤ 0",
        "fields": [],
        "evaluate": _eval_stock_out,
    },
    "receivable_overdue": {
        "label": "应收逾期",
        "description": "客户存在未结清订单，且最早一笔账龄超过设定天数",
        "fields": [
            {"key": "days", "label": "逾期天数", "type": "int", "default": 30, "min": 1, "max": 365},
        ],
        "evaluate": _eval_receivable_overdue,
    },
    "customer_inactive": {
        "label": "客户长期未下单",
        "description": "有历史成交的客户超过设定天数没有新订单，用于流失预警",
        "fields": [
            {"key": "days", "label": "未下单天数", "type": "int", "default": 45, "min": 1, "max": 365},
        ],
        "evaluate": _eval_customer_inactive,
    },
}

ACTIONS: dict[str, dict] = {
    "create_purchase_draft": {
        "label": "生成采购草稿",
        "description": "按供应商聚合生成草稿采购单，已在未完成采购单里的商品自动跳过",
        "fields": [
            {"key": "lead_time_days", "label": "补货周期(天)", "type": "int", "default": 15, "min": 1, "max": 180},
        ],
        "execute": _act_create_purchase_draft,
        "preview": _preview_purchase,
    },
    "notify_feishu": {
        "label": "推送飞书通知",
        "description": "把命中的对象汇总成一张飞书卡片发送到群机器人",
        "fields": [
            {"key": "title", "label": "通知标题", "type": "str", "default": "", "max": 60},
        ],
        "execute": _act_notify_feishu,
    },
}


def options() -> dict:
    """给前端渲染表单用的触发/动作目录。"""
    return {
        "triggers": [
            {"value": key, "label": item["label"], "description": item["description"], "fields": item["fields"]}
            for key, item in TRIGGERS.items()
        ],
        "actions": [
            {
                "value": key,
                "label": item["label"],
                "description": item["description"],
                "fields": item["fields"],
                "preview": "preview" in item,
            }
            for key, item in ACTIONS.items()
        ],
        "max_hits": MAX_HITS,
    }


def _record(
    db: Session,
    rule: AutomationRule,
    status: str,
    summary: str,
    hits: list[dict],
    payload: dict,
    dry_run: bool,
) -> dict:
    """统一出口：试运行不落库，真实执行才写记录并更新规则统计。"""
    result = {
        "rule_id": rule.id,
        "rule_name": rule.name,
        "status": status,
        "summary": summary,
        "hit_count": len(hits),
        "hits": hits[:20],
        "payload": payload,
        "dry_run": dry_run,
    }
    if dry_run:
        return result
    run = AutomationRun(
        rule_id=rule.id,
        rule_name=rule.name,
        status=status,
        summary=summary[:200],
        detail="\n".join(hit.get("label") or hit.get("name") or "" for hit in hits[:50]),
        payload=payload,
    )
    db.add(run)
    rule.last_run_at = datetime.now()
    rule.last_status = status
    rule.run_count = (rule.run_count or 0) + 1
    db.commit()
    db.refresh(run)
    result["run_id"] = run.id
    return result


def run_rule(db: Session, rule: AutomationRule, *, dry_run: bool = False, operator: str = "系统") -> dict:
    """执行一条规则；出错也只记录，不向上抛，避免一条规则拖垮整轮调度。"""
    trigger = TRIGGERS.get(rule.trigger_type)
    action = ACTIONS.get(rule.action_type)
    if trigger is None or action is None:
        return _record(db, rule, "failed", "规则的触发条件或动作类型未注册", [], {}, dry_run)

    try:
        hits = trigger["evaluate"](db, rule.trigger_config or {})
    except Exception as exc:  # noqa: BLE001 单条规则求值失败不应影响其他规则
        logger.exception("自动化规则求值失败：%s", rule.name)
        return _record(db, rule, "failed", f"求值失败：{exc}", [], {}, dry_run)

    hits = hits[:MAX_HITS]
    if not hits:
        return _record(db, rule, "skipped", "本次没有命中对象", [], {}, dry_run)

    if dry_run:
        preview = action["preview"](db, rule, hits) if "preview" in action else {}
        return _record(
            db,
            rule,
            "dry_run",
            f"试运行命中 {len(hits)} 个对象，未执行任何动作",
            hits,
            {"action": rule.action_type, "preview": preview},
            True,
        )

    try:
        payload = action["execute"](db, rule, hits, rule.action_config or {}, operator)
        db.commit()
    except AutomationError as exc:
        db.rollback()
        return _record(db, rule, "failed", str(exc), hits, {}, False)
    except Exception as exc:  # noqa: BLE001 动作失败要留痕而不是抛给调用方
        db.rollback()
        logger.exception("自动化规则执行失败：%s", rule.name)
        return _record(db, rule, "failed", f"执行失败：{exc}", hits, {}, False)

    if payload.get("created_orders"):
        created = payload["created_orders"]
        status = "success"
        summary = f"已生成 {len(created)} 张采购草稿，共 {sum(o['item_count'] for o in created)} 个商品"
    elif payload.get("sent"):
        status = "success"
        summary = f"已推送飞书通知，包含 {payload.get('count', len(hits))} 个对象"
    elif payload.get("skipped"):
        status = "skipped"
        summary = "命中对象均无需处理：" + "、".join(payload["skipped"][:3])
    else:
        status = "success"
        summary = f"已处理 {len(hits)} 个对象"
    return _record(db, rule, status, summary, hits, payload, False)


def run_due(db: Session, operator: str = "系统") -> list[dict]:
    """调度器入口：按冷却时间挑出到期规则并执行。"""
    rules = db.scalars(
        select(AutomationRule).where(AutomationRule.enabled == True).order_by(AutomationRule.id)  # noqa: E712
    ).all()
    now = datetime.now()
    results = []
    for rule in rules:
        cooldown = max(int(rule.cooldown_minutes or 0), 0) * 60
        if rule.last_run_at and (now - rule.last_run_at).total_seconds() < cooldown:
            continue
        results.append(run_rule(db, rule, operator=operator))
    return results
