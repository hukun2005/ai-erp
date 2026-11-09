"""客户信用额度管控。

额度只在「确认出库」这一刻校验，因为草稿和待审批订单还没占用库存与应收；
占用金额取未结清订单余额（已确认订单 − 已确认退货 − 已收款），
与应收模块口径一致，避免两处算法各说各话。

策略由客户等级配置决定（见 services/credit_policy.py），未配置时回落到
CREDIT_POLICY：
- off：完全不检查；
- warn：允许出库，但在响应里带 credit_warning，由前端提示；
- block：超额直接拦截，必须调整额度、回款或拿到特批后才能确认。
"""
from sqlalchemy.orm import Session

from ..models import Customer, SalesOrder
from . import credit_policy, receivables


def exposure(db: Session, customer_id: int) -> dict:
    """客户当前占用额度 = 未结清订单余额合计。"""
    rows = receivables.open_orders(db, customer_id)
    outstanding = round(sum(row["balance"] for row in rows), 2)
    return {"outstanding": outstanding, "open_orders": len(rows)}


def summary(db: Session, customer: Customer) -> dict:
    """额度全景：额度、已占用、可用、使用率与是否超限。"""
    limit = round(float(customer.credit_limit or 0), 2)
    used = exposure(db, customer.id)["outstanding"]
    over = limit > 0 and used > limit
    return {
        "customer_id": customer.id,
        "customer_name": customer.name,
        "credit_limit": limit,
        "outstanding": used,
        "available": round(limit - used, 2) if limit > 0 else 0.0,
        "enabled": limit > 0,
        "over_limit": over,
        "usage_percent": round(used / limit * 100, 1) if limit > 0 else 0.0,
        "policy": credit_policy.policy_for(db, customer),
    }


def check_order(db: Session, order: SalesOrder, policy: str | None = None) -> dict:
    """订单确认前的额度校验。

    返回 blocked（是否拦截）、warn（是否提示）与可直接展示的 message。
    未设置额度（credit_limit=0）视为不管控，避免给存量客户凭空加上限制。
    """
    customer = db.get(Customer, order.customer_id)
    if customer is None:
        return {"checked": False, "blocked": False, "warn": False, "message": ""}

    policy = policy or credit_policy.policy_for(db, customer)
    if policy == "off":
        return {"checked": False, "blocked": False, "warn": False, "message": ""}

    info = summary(db, customer)
    if not info["enabled"]:
        return {"checked": True, "blocked": False, "warn": False, "message": "", **info}

    amount = round(float(order.total_amount or 0), 2)
    projected = round(info["outstanding"] + amount, 2)
    over_amount = round(projected - info["credit_limit"], 2)
    if over_amount <= 0:
        return {"checked": True, "blocked": False, "warn": False, "message": "", **info}

    message = (
        f"客户「{customer.name}」信用额度 {info['credit_limit']:,.2f}，"
        f"已占用 {info['outstanding']:,.2f}，本单 {amount:,.2f}，"
        f"合计超出 {over_amount:,.2f}"
    )
    blocked = policy == "block"
    return {
        "checked": True,
        "blocked": blocked,
        "warn": not blocked,
        "message": message,
        "over_amount": over_amount,
        **info,
    }
