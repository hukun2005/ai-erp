"""库存自动预警：库存跌破阈值后自动推送飞书，并对同一商品做冷却去重。

触发时机由业务路由在库存减少（手工出库/盘点调减、销售出库、采购入库撤销）后调用，
推送失败不影响业务事务（业务已提交），仅记录失败审计。
"""
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record
from ..models import AlertRule, AlertState, Product
from . import feishu, notify_config

logger = logging.getLogger(__name__)

CONDITION_LABELS = {"below_safety": "低于安全库存", "out_of_stock": "已缺货"}


def get_rule(db: Session, create: bool = True) -> AlertRule | None:
    row = db.scalars(select(AlertRule).order_by(AlertRule.id)).first()
    if row is None and create:
        row = AlertRule()
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def update_rule(
    db: Session,
    *,
    enabled: bool,
    condition: str,
    cooldown_minutes: int,
    include_replenishment: bool,
    username: str,
) -> AlertRule:
    row = get_rule(db)
    row.enabled = enabled
    row.condition = condition
    row.cooldown_minutes = cooldown_minutes
    row.include_replenishment = include_replenishment
    row.updated_by = username
    row.updated_at = datetime.now()
    db.commit()
    db.refresh(row)
    return row


def _hit(product: Product, condition: str) -> bool:
    if product.stock_qty <= 0:
        return True if condition == "out_of_stock" else product.stock_qty <= product.safety_stock
    if condition == "out_of_stock":
        return False
    return product.stock_qty <= product.safety_stock


def pending_count(db: Session, condition: str = "below_safety") -> int:
    products = db.scalars(select(Product).where(Product.is_active == True)).all()  # noqa: E712
    return len([p for p in products if _hit(p, condition)])


def _card(rule: AlertRule, products: list[Product], username: str, scene: str) -> dict:
    lines = [
        f"**触发条件**：{CONDITION_LABELS.get(rule.condition, rule.condition)}",
        f"**触发商品**：{len(products)} 个",
    ]
    for p in products[:10]:
        gap = p.safety_stock - p.stock_qty
        detail = f"库存 {p.stock_qty}{p.unit}（安全库存 {p.safety_stock}）"
        if p.stock_qty <= 0:
            detail += "，已缺货"
        elif gap > 0:
            detail += f"，缺口 {gap}{p.unit}"
        lines.append(f"- {p.sku} {p.name}：{detail}")
    if len(products) > 10:
        lines.append(f"- 其余 {len(products) - 10} 个商品略")
    if rule.include_replenishment:
        total_gap = sum(max(p.safety_stock - p.stock_qty, 1) for p in products)
        lines.append(f"\n**补货建议**：合计约需补 {total_gap} 件，可在「AI 智能 › 补货建议」生成采购单")
    return feishu.build_card(
        title="【AI-ERP】库存自动预警",
        template="red",
        lines=lines,
        footer=f"{scene} · 触发人 {username} · {datetime.now():%Y-%m-%d %H:%M}",
    )


def check_and_notify(
    db: Session,
    product_ids: list[int],
    username: str = "",
    scene: str = "库存变动",
) -> dict:
    """库存减少后检查是否触发自动预警，触发则推送飞书。永不抛异常。"""
    try:
        rule = get_rule(db)
        if rule is None or not rule.enabled:
            return {"sent": False, "reason": "disabled"}

        now = datetime.now()
        triggered: list[Product] = []
        for pid in {pid for pid in product_ids if pid}:
            product = db.get(Product, pid)
            if product is None or not product.is_active or not _hit(product, rule.condition):
                continue
            state = db.scalars(select(AlertState).where(AlertState.product_id == pid)).first()
            if state and (now - state.last_alert_at).total_seconds() < rule.cooldown_minutes * 60:
                continue
            triggered.append(product)

        if not triggered:
            return {"sent": False, "reason": "no_hit"}

        names = "、".join(p.name for p in triggered[:3])
        condition_label = CONDITION_LABELS.get(rule.condition, rule.condition)

        # 站内消息：无论飞书是否配置，库存预警都进顶栏铃铛（admin + 仓管）
        from . import inbox

        stock_users = inbox.users_by_permission(db, "inventory:view")
        inbox.push(
            db,
            stock_users,
            category="stock",
            title=f"库存预警：{len(triggered)} 个商品",
            body=(
                f"触发条件：{condition_label}\n"
                f"触发商品：{len(triggered)} 个（{names}{'…' if len(triggered) > 3 else ''}）；场景：{scene}"
            ),
            ref_type="product",
            ref_id=0,
        )

        for p in triggered:
            state = db.scalars(select(AlertState).where(AlertState.product_id == p.id)).first()
            if state is None:
                state = AlertState(product_id=p.id)
                db.add(state)
            state.last_alert_at = now
            state.last_stock_qty = p.stock_qty
        rule.last_triggered_at = now
        db.commit()

        # 飞书推送：可选、失败不影响已落库的站内消息与去重状态
        feishu_sent = False
        feishu_error = ""
        cfg = notify_config.effective(db)
        if notify_config.ready(cfg):
            try:
                card = _card(rule, triggered, username, scene)
                feishu.send_card(card, cfg["webhook_url"], cfg["secret"])
                feishu_sent = True
            except feishu.FeishuError as exc:
                feishu_error = str(exc)

        record(
            username=username,
            role="",
            action="库存自动预警",
            resource="库存预警",
            detail=(
                f"{len(triggered)} 个商品触发{condition_label}"
                f"（{names}{'…' if len(triggered) > 3 else ''}）："
                f"站内通知 {len(stock_users)} 人"
                + ("，已推送飞书" if feishu_sent else f"，飞书未推送{f'（{feishu_error}）' if feishu_error else ''}")
                + f"；场景：{scene}"
            ),
        )
        return {
            "sent": True,
            "count": len(triggered),
            "products": [p.name for p in triggered],
            "inbox_count": len(stock_users),
            "feishu_sent": feishu_sent,
            "message": (
                f"已推送库存预警：站内通知 {len(stock_users)} 人"
                + ("、飞书群" if feishu_sent else "")
                + f"（{len(triggered)} 个商品）"
            ),
        }
    except feishu.FeishuError as exc:
        logger.warning("库存自动预警推送失败：%s", exc)
        record(
            username=username,
            role="",
            action="库存自动预警",
            resource="库存预警",
            detail=f"推送失败：{exc}；场景：{scene}",
            result="failed",
            status_code=502,
        )
        return {"sent": False, "reason": str(exc)}
    except Exception:  # noqa: BLE001 自动预警异常不影响主业务
        logger.exception("库存自动预警执行失败")
        db.rollback()
        return {"sent": False, "reason": "internal_error"}
