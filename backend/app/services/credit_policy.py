"""信用策略与超额特批。

策略优先级：客户等级专属配置 → 全局默认（customer_level="*"）→ .env 的 CREDIT_POLICY。
超额时不允许确认，但可以发起「特批」；审批通过前该单始终被拦截。
"""
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import CreditOverride, CreditPolicy, Customer, SalesOrder

VALID_POLICIES = ("off", "warn", "block")
GLOBAL_LEVEL = "*"


def policy_for(db: Session, customer: Customer | None) -> str:
    """解析某个客户适用的策略。"""
    level = (customer.level or "").strip().upper() if customer else ""
    if level:
        row = db.scalar(select(CreditPolicy).where(CreditPolicy.customer_level == level))
        if row is not None:
            return row.policy
    row = db.scalar(select(CreditPolicy).where(CreditPolicy.customer_level == GLOBAL_LEVEL))
    return row.policy if row is not None else settings.CREDIT_POLICY


def list_policies(db: Session) -> dict:
    rows = db.scalars(select(CreditPolicy).order_by(CreditPolicy.customer_level)).all()
    configured = {row.customer_level: row.policy for row in rows}
    return {
        "default": configured.get(GLOBAL_LEVEL, settings.CREDIT_POLICY),
        "levels": configured,
        "valid": list(VALID_POLICIES),
    }


def save_policies(db: Session, policies: dict[str, str], operator: str) -> dict:
    """批量保存策略；非法值直接拒绝，避免写进库里才发现。"""
    for level, policy in policies.items():
        value = (policy or "").strip().lower()
        if value not in VALID_POLICIES:
            raise HTTPException(
                status_code=400,
                detail=f"客户等级 {level} 的策略只能是 {'/'.join(VALID_POLICIES)}",
            )
    for level, policy in policies.items():
        key = (level or GLOBAL_LEVEL).strip().upper() if level != GLOBAL_LEVEL else GLOBAL_LEVEL
        row = db.scalar(select(CreditPolicy).where(CreditPolicy.customer_level == key))
        if row is None:
            row = CreditPolicy(customer_level=key, policy=policy, updated_by=operator)
            db.add(row)
        else:
            row.policy = policy.strip().lower()
            row.updated_by = operator
        row.updated_at = datetime.now()
    db.flush()
    return list_policies(db)


def approved_override(db: Session, order_id: int, order_type: str = "sales") -> CreditOverride | None:
    return db.scalar(
        select(CreditOverride)
        .where(CreditOverride.order_type == order_type)
        .where(CreditOverride.order_id == order_id)
        .where(CreditOverride.status == "approved")
    )


def latest_override(db: Session, order_id: int, order_type: str = "sales") -> CreditOverride | None:
    return db.scalar(
        select(CreditOverride)
        .where(CreditOverride.order_type == order_type)
        .where(CreditOverride.order_id == order_id)
        .order_by(CreditOverride.id.desc())
    )


def request_override(
    db: Session, order: SalesOrder, user, reason: str, over_amount: float
) -> CreditOverride:
    """发起特批：同一订单只保留一条待审记录，重复申请直接返回已有记录。"""
    existing = latest_override(db, order.id)
    if existing is not None and existing.status == "pending":
        return existing
    row = CreditOverride(
        order_type="sales",
        order_id=order.id,
        customer_id=order.customer_id,
        amount=round(float(order.total_amount or 0), 2),
        over_amount=round(float(over_amount or 0), 2),
        reason=(reason or "")[:300],
        status="pending",
        requested_by=user.username,
        requested_at=datetime.now(),
    )
    db.add(row)
    db.flush()
    return row


def decide_override(db: Session, row: CreditOverride, action: str, user, comment: str = "") -> CreditOverride:
    if row.status != "pending":
        raise HTTPException(status_code=400, detail="该特批申请已处理")
    if action == "approve":
        row.status = "approved"
    elif action == "reject":
        row.status = "rejected"
    else:
        raise HTTPException(status_code=400, detail=f"不支持的操作：{action}")
    row.comment = (comment or "")[:300]
    row.decided_by = user.username
    row.decided_at = datetime.now()
    db.flush()
    return row
