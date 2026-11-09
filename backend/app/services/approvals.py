"""订单审批流：草稿 → 待审批 → 已通过 / 驳回。

审批动作逐条写入 ApprovalLog，订单本身只更新 status，
这样既能展示完整审批轨迹，也不必改动既有订单表结构。
"""
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    ApprovalLog,
    OutboundOrder,
    Payment,
    PurchaseOrder,
    PurchaseRequisition,
    PurchaseReturn,
    SalesOrder,
    SalesReturn,
    StocktakeOrder,
    SupplierPayment,
)

ACTION_LABELS = {"submit": "提交审批", "approve": "审批通过", "reject": "审批驳回"}


def log_action(
    db: Session,
    order_type: str,
    order_id: int,
    action: str,
    user,
    comment: str = "",
    level: int = 0,
    principal: str = "",
) -> ApprovalLog:
    entry = ApprovalLog(
        order_type=order_type,
        order_id=order_id,
        action=action,
        level=int(level or 0),
        operator=user.username,
        principal=(principal or user.username),
        role=user.role,
        comment=(comment or "")[:300],
        created_at=datetime.now(),
    )
    db.add(entry)

    # 提交审批：给持有该单据审批权限的人（含 admin）发一条站内消息，和飞书并存
    if action == "submit":
        from . import approval_flow, inbox

        permission = approval_flow.DEFAULT_PERMISSIONS.get(order_type, "")
        if permission:
            label = approval_flow.DOC_LABELS.get(order_type, order_type)
            inbox.push(
                db,
                inbox.users_by_permission(db, permission),
                category="approval",
                title=f"待审批：{label}",
                body=f"{label}（ID {order_id}）已提交审批，请尽快处理。",
                ref_type=order_type,
                ref_id=order_id,
            )

    return entry


def order_logs(db: Session, order_type: str, order_id: int) -> list[ApprovalLog]:
    return list(
        db.scalars(
            select(ApprovalLog)
            .where(ApprovalLog.order_type == order_type)
            .where(ApprovalLog.order_id == order_id)
            .order_by(ApprovalLog.id)
        ).all()
    )


def _items_summary(items) -> str:
    summary = "、".join(
        f"{item.product.name if getattr(item, 'product', None) else item.product_id}×{item.quantity}"
        for item in items[:3]
    )
    if len(items) > 3:
        summary += f" 等 {len(items)} 项"
    return summary


def _entry(order_type, order, order_no, partner_name, amount, when, summary) -> dict:
    return {
        "order_type": order_type,
        "order_id": order.id,
        "order_no": order_no,
        "partner_name": partner_name,
        "total_amount": round(float(amount or 0), 2),
        "created_by": getattr(order, "created_by", ""),
        "order_date": when,
        "item_summary": summary,
        "status": order.status,
        # 多级审批进度：老单据这两个字段为 0，前端按单级展示
        "approval_required": int(getattr(order, "approval_required", 0) or 0),
        "approval_approved": int(getattr(order, "approval_approved", 0) or 0),
        "current_level": int(getattr(order, "approval_approved", 0) or 0) + 1,
    }


def pending_orders(db: Session) -> list[dict]:
    """待审批列表：订单、出库、退货、收付款、采购申请与盘点差异，按提交时间倒序。"""
    items: list[dict] = []

    # 1) 订单：销售 / 采购
    for order_type, model in (("sales", SalesOrder), ("purchase", PurchaseOrder)):
        for order in db.scalars(
            select(model).where(model.status == "pending").order_by(model.id.desc())
        ).all():
            partner = order.customer if order_type == "sales" else order.supplier
            items.append(
                _entry(
                    order_type, order, order.order_no,
                    partner.name if partner else "",
                    order.total_amount, order.order_date, _items_summary(order.items),
                )
            )

    # 2) 销售出库（待审批时才出库）
    for outbound in db.scalars(
        select(OutboundOrder).where(OutboundOrder.status == "pending").order_by(OutboundOrder.id.desc())
    ).all():
        items.append(
            _entry(
                "outbound", outbound, outbound.outbound_no,
                outbound.customer.name if outbound.customer else "",
                outbound.total_amount, outbound.created_at, _items_summary(outbound.items),
            )
        )

    # 3) 退货：销售 / 采购
    for order_type, model, partner_attr in (
        ("sales_return", SalesReturn, "customer"),
        ("purchase_return", PurchaseReturn, "supplier"),
    ):
        for ret in db.scalars(select(model).where(model.status == "pending").order_by(model.id.desc())).all():
            partner = getattr(ret, partner_attr, None)
            items.append(
                _entry(
                    order_type, ret, ret.return_no,
                    partner.name if partner else "",
                    ret.total_amount, ret.created_at, _items_summary(ret.items),
                )
            )

    # 4) 收付款单
    for order_type, model, partner_attr in (
        ("payment", Payment, "customer"),
        ("supplier_payment", SupplierPayment, "supplier"),
    ):
        for pay in db.scalars(select(model).where(model.status == "pending").order_by(model.id.desc())).all():
            partner = getattr(pay, partner_attr, None)
            items.append(
                _entry(
                    order_type, pay, pay.payment_no,
                    partner.name if partner else "",
                    pay.amount, pay.pay_date, pay.remark or "",
                )
            )

    # 5) 采购申请
    for req in db.scalars(
        select(PurchaseRequisition).where(PurchaseRequisition.status == "pending").order_by(PurchaseRequisition.id.desc())
    ).all():
        items.append(
            _entry(
                "requisition", req, req.req_no, "",
                sum(float(item.amount or 0) for item in req.items),
                req.created_at, _items_summary(req.items),
            )
        )

    # 6) 盘点差异
    for stocktake in db.scalars(
        select(StocktakeOrder).where(StocktakeOrder.status == "pending").order_by(StocktakeOrder.id.desc())
    ).all():
        diffs = [item for item in stocktake.items if item.diff_qty]
        items.append(
            _entry(
                "stocktake", stocktake, stocktake.stocktake_no,
                stocktake.warehouse.name if stocktake.warehouse else "",
                0.0, stocktake.submitted_at or stocktake.created_at,
                f"差异 {len(diffs)} 项",
            )
        )

    items.sort(key=lambda row: row["order_date"], reverse=True)
    return items


def ensure_status(order, allowed: tuple[str, ...], action_label: str) -> None:
    if order.status not in allowed:
        current = {"draft": "草稿", "pending": "待审批"}.get(order.status, order.status)
        raise HTTPException(status_code=400, detail=f"当前状态为「{current}」，无法{action_label}")
