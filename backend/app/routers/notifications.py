"""飞书通知：向管理层群推送库存预警、待决策事项与经营日报。

接入方式：管理员在「飞书通知」页面填写群机器人 Webhook（可选签名密钥）并保存，
配置存于数据库、保存即生效，客户无需修改代码或重启服务。
"""
import logging
from datetime import date, datetime, time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import require_perm
from ..models import FeishuConfig, Product, PurchaseOrder, SalesOrder, SalesOrderItem, User
from ..services import alerts, feishu, notify_config
from ..services.feishu import FeishuError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["飞书通知"])

KIND_META: dict[str, dict] = {
    "stock_alert": {
        "name": "库存预警",
        "desc": "缺货与低于安全库存的商品，用于催办补货决策",
        "template": "red",
    },
    "pending": {
        "name": "待决策事项",
        "desc": "待确认的销售单与待入库的采购单，供管理层审批推进",
        "template": "orange",
    },
    "daily": {
        "name": "经营日报",
        "desc": "当日销售额、热销商品与库存健康度摘要",
        "template": "blue",
    },
}


class NotifySend(BaseModel):
    kind: Literal["stock_alert", "pending", "daily"] = "stock_alert"
    dry_run: bool = False


class FeishuConfigIn(BaseModel):
    webhook_url: str
    # None 表示保持原有密钥不变；"" 表示清除密钥；非空则更新
    secret: str | None = None
    enabled: bool = True


class AlertRuleIn(BaseModel):
    enabled: bool = False
    condition: Literal["below_safety", "out_of_stock"] = "below_safety"
    cooldown_minutes: int = 120
    include_replenishment: bool = True


class FeishuTestIn(BaseModel):
    webhook_url: str | None = None
    secret: str | None = None


_saved_config = notify_config.saved
_effective_config = notify_config.effective
_is_ready = notify_config.ready
_config_payload = notify_config.payload


def _test_card(user: User, note: str = "") -> dict:
    lines = [
        f"**操作人**：{user.username}（{user.role}）",
        "**说明**：收到本消息表示 ERP 与飞书机器人已打通，可推送库存预警与待决策事项。",
    ]
    if note:
        lines.append(f"**备注**：{note}")
    return feishu.build_card(
        title="【AI-ERP】飞书连接测试",
        template="turquoise",
        lines=lines,
        footer=f"发送时间 {datetime.now():%Y-%m-%d %H:%M}",
    )


def _money(value: float) -> str:
    return f"¥{value:,.2f}"


def _build_stock_alert(db: Session) -> tuple[str, list[str]]:
    products = db.scalars(select(Product).where(Product.is_active == True)).all()  # noqa: E712
    out_of_stock = sorted(
        [p for p in products if p.stock_qty <= 0], key=lambda p: p.safety_stock, reverse=True
    )
    low_stock = sorted(
        [p for p in products if 0 < p.stock_qty <= p.safety_stock],
        key=lambda p: p.stock_qty - p.safety_stock,
    )
    stock_value = sum(p.stock_qty * p.cost_price for p in products)

    lines = [
        f"**库存总价值**：{_money(stock_value)}",
        f"**缺货商品**：{len(out_of_stock)} 个　**低于安全库存**：{len(low_stock)} 个",
    ]
    if out_of_stock:
        lines.append("\n**缺货 TOP**")
        for p in out_of_stock[:5]:
            lines.append(f"- {p.sku} {p.name}：0 {p.unit}（安全库存 {p.safety_stock}）")
    if low_stock:
        lines.append("\n**预警 TOP**")
        for p in low_stock[:5]:
            lines.append(
                f"- {p.sku} {p.name}：{p.stock_qty} {p.unit}"
                f"（安全库存 {p.safety_stock}，缺 {p.safety_stock - p.stock_qty}）"
            )
    if not out_of_stock and not low_stock:
        lines.append("\n当前库存状态良好，无需补货决策。")
    else:
        lines.append("\n建议：优先对缺货商品下达采购单，预警商品按周转率评估补货批量。")
    summary = f"缺货 {len(out_of_stock)} 个，低于安全库存 {len(low_stock)} 个"
    return summary, lines


def _build_pending(db: Session) -> tuple[str, list[str]]:
    sales = db.scalars(select(SalesOrder).where(SalesOrder.status == "draft")).all()
    purchases = db.scalars(select(PurchaseOrder).where(PurchaseOrder.status == "ordered")).all()
    to_ship = db.scalars(select(SalesOrder).where(SalesOrder.status == "confirmed")).all()

    sales_amount = sum(o.total_amount for o in sales)
    purchase_amount = sum(o.total_amount for o in purchases)
    lines = [
        f"**待确认销售单**：{len(sales)} 单，合计 {_money(sales_amount)}",
        f"**待入库采购单**：{len(purchases)} 单，合计 {_money(purchase_amount)}",
        f"**待发货销售单**：{len(to_ship)} 单，合计 {_money(sum(o.total_amount for o in to_ship))}",
    ]
    top_sales = sorted(sales, key=lambda o: o.total_amount, reverse=True)[:3]
    if top_sales:
        lines.append("\n**待确认销售单 TOP**")
        for o in top_sales:
            customer = o.customer.name if o.customer else "-"
            lines.append(f"- {o.order_no} {customer}：{_money(o.total_amount)}")
    top_purchase = sorted(purchases, key=lambda o: o.total_amount, reverse=True)[:3]
    if top_purchase:
        lines.append("\n**待入库采购单 TOP**")
        for o in top_purchase:
            supplier = o.supplier.name if o.supplier else "-"
            lines.append(f"- {o.order_no} {supplier}：{_money(o.total_amount)}")
    if not sales and not purchases and not to_ship:
        lines.append("\n当前没有待决策的订单，全部流程已处理完毕。")
    summary = (
        f"待确认销售 {len(sales)} 单，待入库采购 {len(purchases)} 单，待发货 {len(to_ship)} 单"
    )
    return summary, lines


def _build_daily(db: Session) -> tuple[str, list[str]]:
    today = date.today()
    start = datetime.combine(today, time.min)
    orders = db.scalars(
        select(SalesOrder)
        .where(SalesOrder.order_date >= start)
        .where(SalesOrder.status != "cancelled")
    ).all()
    amount = sum(o.total_amount for o in orders)

    rows = db.execute(
        select(Product.sku, Product.name, func.sum(SalesOrderItem.quantity).label("qty"))
        .join(SalesOrderItem, SalesOrderItem.product_id == Product.id)
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
        .where(SalesOrder.order_date >= start)
        .where(SalesOrder.status != "cancelled")
        .group_by(Product.id)
        .order_by(func.sum(SalesOrderItem.quantity).desc())
        .limit(3)
    ).all()

    products = db.scalars(select(Product).where(Product.is_active == True)).all()  # noqa: E712
    stock_value = sum(p.stock_qty * p.cost_price for p in products)
    low_count = len([p for p in products if p.stock_qty <= p.safety_stock])
    pending_sales = db.scalar(
        select(func.count()).select_from(SalesOrder).where(SalesOrder.status == "draft")
    ) or 0

    lines = [
        f"**今日销售额**：{_money(amount)}（{len(orders)} 单）",
        f"**库存总价值**：{_money(stock_value)}　**预警商品**：{low_count} 个",
        f"**待确认销售单**：{pending_sales} 单",
    ]
    if rows:
        lines.append("\n**今日热销 TOP3**")
        for sku, name, qty in rows:
            lines.append(f"- {sku} {name}：{int(qty)} 件")
    else:
        lines.append("\n今日暂无销售成交记录。")
    summary = f"今日销售 {_money(amount)}，{len(orders)} 单；预警商品 {low_count} 个"
    return summary, lines


BUILDERS = {
    "stock_alert": _build_stock_alert,
    "pending": _build_pending,
    "daily": _build_daily,
}


def _compose(kind: str, db: Session) -> dict:
    meta = KIND_META[kind]
    summary, lines = BUILDERS[kind](db)
    card = feishu.build_card(
        title=f"【AI-ERP】{meta['name']}",
        template=meta["template"],
        lines=lines,
        footer=f"{meta['desc']} · 生成时间 {datetime.now():%Y-%m-%d %H:%M}",
    )
    return {"kind": kind, "name": meta["name"], "summary": summary, "card": card}


@router.get("/status", summary="飞书通知配置状态")
def status(db: Session = Depends(get_db), user: User = Depends(require_perm("notify:manage"))):
    cfg = _effective_config(db)
    return {
        "configured": _is_ready(cfg),
        "webhook": feishu.masked(cfg["webhook_url"]),
        "secret_configured": bool(cfg["secret"]),
        "enabled": cfg["enabled"],
        "source": cfg["source"],
        "kinds": [
            {"key": key, "name": meta["name"], "desc": meta["desc"]}
            for key, meta in KIND_META.items()
        ],
    }


@router.get("/config", summary="读取飞书接入配置")
def get_config(db: Session = Depends(get_db), user: User = Depends(require_perm("notify:manage"))):
    return _config_payload(db)


@router.put("/config", summary="保存飞书接入配置（保存即生效）")
def save_config(
    payload: FeishuConfigIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("notify:manage")),
):
    try:
        webhook_url = feishu.validate_webhook(payload.webhook_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    secret = payload.secret
    if secret is not None:
        secret = secret.strip()
        if len(secret) > 200:
            raise HTTPException(status_code=400, detail="签名密钥过长，最多 200 个字符")

    row = _saved_config(db)
    if row is None:
        row = FeishuConfig()
        db.add(row)
    row.webhook_url = webhook_url
    if secret is not None:
        row.secret = secret
    row.is_active = payload.enabled
    row.updated_by = user.username
    row.updated_at = datetime.now()
    db.commit()
    db.refresh(row)

    record(
        username=user.username,
        role=user.role,
        action="保存飞书配置",
        resource="飞书机器人",
        detail=(
            f"{feishu.masked(row.webhook_url)}；"
            f"签名校验：{'已开启' if row.secret else '未开启'}；"
            f"状态：{'启用' if row.is_active else '停用'}"
        ),
        request=request,
    )
    return {"ok": True, "message": "飞书机器人配置已保存并立即生效", **_config_payload(db)}


@router.delete("/config", summary="清除飞书接入配置")
def clear_config(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("notify:manage")),
):
    row = _saved_config(db)
    if row is not None:
        db.delete(row)
        db.commit()
    record(
        username=user.username,
        role=user.role,
        action="清除飞书配置",
        resource="飞书机器人",
        detail="已清除页面保存的 Webhook 与签名密钥",
        request=request,
    )
    return {"ok": True, "message": "已清除飞书机器人配置", **_config_payload(db)}


def _alert_rule_payload(db: Session) -> dict:
    rule = alerts.get_rule(db)
    return {
        "enabled": bool(rule.enabled),
        "condition": rule.condition,
        "condition_label": alerts.CONDITION_LABELS.get(rule.condition, rule.condition),
        "cooldown_minutes": rule.cooldown_minutes,
        "include_replenishment": bool(rule.include_replenishment),
        "last_triggered_at": rule.last_triggered_at.isoformat(timespec="seconds")
        if rule.last_triggered_at
        else "",
        "updated_by": rule.updated_by,
        "updated_at": rule.updated_at.isoformat(timespec="seconds") if rule.updated_at else "",
        "pending_count": alerts.pending_count(db, rule.condition),
        "feishu_ready": _is_ready(_effective_config(db)),
    }


@router.get("/alert-rule", summary="读取库存自动预警规则")
def get_alert_rule(
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("notify:manage")),
):
    return _alert_rule_payload(db)


@router.put("/alert-rule", summary="保存库存自动预警规则")
def save_alert_rule(
    payload: AlertRuleIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("notify:manage")),
):
    if not 1 <= payload.cooldown_minutes <= 1440:
        raise HTTPException(status_code=400, detail="冷却时间需在 1~1440 分钟之间")
    rule = alerts.update_rule(
        db,
        enabled=payload.enabled,
        condition=payload.condition,
        cooldown_minutes=payload.cooldown_minutes,
        include_replenishment=payload.include_replenishment,
        username=user.username,
    )
    record(
        username=user.username,
        role=user.role,
        action="保存预警规则",
        resource="库存自动预警",
        detail=(
            f"{'启用' if rule.enabled else '停用'}；"
            f"触发条件：{alerts.CONDITION_LABELS.get(rule.condition, rule.condition)}；"
            f"冷却 {rule.cooldown_minutes} 分钟；附带补货建议：{'是' if rule.include_replenishment else '否'}"
        ),
        request=request,
    )
    return {"ok": True, "message": "自动预警规则已保存", **_alert_rule_payload(db)}


@router.get("/preview", summary="预览飞书通知内容")
def preview(
    kind: str = Query("stock_alert"),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("notify:manage")),
):
    if kind not in KIND_META:
        raise HTTPException(status_code=400, detail="不支持的通知类型")
    try:
        return _compose(kind, db)
    except Exception:  # noqa: BLE001
        logger.exception("生成飞书通知预览失败：类型=%s", kind)
        raise HTTPException(status_code=500, detail="生成通知内容失败，请稍后重试")


@router.post("/config/test", summary="用当前填写（未保存）的配置发送测试消息")
def test_config(
    request: Request,
    payload: FeishuTestIn | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("notify:manage")),
):
    cfg = _effective_config(db)
    webhook_url = cfg["webhook_url"]
    secret = cfg["secret"]
    unsaved = bool(payload and payload.webhook_url)
    if unsaved:
        try:
            webhook_url = feishu.validate_webhook(payload.webhook_url or "")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        secret = (payload.secret or "").strip()

    if not webhook_url:
        raise HTTPException(status_code=400, detail="请先填写 Webhook 地址再测试连接")

    card = _test_card(user, note="使用页面当前填写的地址测试，尚未保存" if unsaved else "")
    try:
        feishu.send_card(card, webhook_url, secret)
    except FeishuError as exc:
        record(
            username=user.username,
            role=user.role,
            action="飞书通知测试",
            resource="飞书连接测试",
            detail=f"{feishu.masked(webhook_url)}；发送失败：{exc}",
            result="failed",
            request=request,
            status_code=502,
        )
        raise HTTPException(status_code=502, detail=str(exc))

    record(
        username=user.username,
        role=user.role,
        action="飞书通知测试",
        resource="飞书连接测试",
        detail=f"{feishu.masked(webhook_url)}；测试消息发送成功",
        request=request,
    )
    return {"ok": True, "message": "测试消息已发送，请到飞书群确认"}


@router.post("/send", summary="发送飞书通知给管理层")
def send(
    payload: NotifySend,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("notify:manage")),
):
    if payload.kind not in KIND_META:
        raise HTTPException(status_code=400, detail="不支持的通知类型")
    try:
        message = _compose(payload.kind, db)
    except Exception:  # noqa: BLE001
        logger.exception("生成飞书通知失败：类型=%s", payload.kind)
        record(
            username=user.username,
            role=user.role,
            action="发送飞书通知",
            resource=KIND_META[payload.kind]["name"],
            detail="通知内容生成失败",
            result="failed",
            request=request,
            status_code=500,
        )
        raise HTTPException(status_code=500, detail="生成通知内容失败，请稍后重试")

    name = KIND_META[payload.kind]["name"]
    if payload.dry_run:
        record(
            username=user.username,
            role=user.role,
            action="发送飞书通知",
            resource=name,
            detail=f"模拟发送：{message['summary']}",
            request=request,
        )
        return {**message, "ok": True, "dry_run": True, "message": "模拟发送成功（未调用飞书）"}

    cfg = _effective_config(db)
    if not _is_ready(cfg):
        raise HTTPException(
            status_code=400,
            detail="尚未接入飞书：请先在「飞书通知」页面填写机器人 Webhook 并保存",
        )

    try:
        result = feishu.send_card(message["card"], cfg["webhook_url"], cfg["secret"])
    except FeishuError as exc:
        logger.warning("飞书通知发送失败：%s", exc)
        record(
            username=user.username,
            role=user.role,
            action="发送飞书通知",
            resource=name,
            detail=f"{message['summary']}；发送失败：{exc}",
            result="failed",
            request=request,
            status_code=502,
        )
        raise HTTPException(status_code=502, detail=str(exc))
    record(
        username=user.username,
        role=user.role,
        action="发送飞书通知",
        resource=name,
        detail=f"已推送至管理层群：{message['summary']}",
        request=request,
    )
    return {**message, "ok": result["ok"], "dry_run": False, "message": "已发送到飞书群"}


@router.post("/test", summary="发送飞书测试消息")
def send_test(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("notify:manage")),
):
    cfg = _effective_config(db)
    if not _is_ready(cfg):
        raise HTTPException(
            status_code=400,
            detail="尚未接入飞书：请先在「飞书通知」页面填写机器人 Webhook 并保存",
        )
    try:
        feishu.send_card(_test_card(user), cfg["webhook_url"], cfg["secret"])
    except FeishuError as exc:
        record(
            username=user.username,
            role=user.role,
            action="飞书通知测试",
            resource="飞书连接测试",
            detail=f"发送失败：{exc}",
            result="failed",
            request=request,
            status_code=502,
        )
        raise HTTPException(status_code=502, detail=str(exc))

    record(
        username=user.username,
        role=user.role,
        action="飞书通知测试",
        resource="飞书连接测试",
        detail="测试消息发送成功",
        request=request,
    )
    return {"ok": True, "message": "测试消息已发送到飞书群"}
