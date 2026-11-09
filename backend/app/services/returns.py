"""退货服务：销售退货冲减应收，采购退货冲减应付，同时回冲库存。

口径约定：
- 只有「已确认」的退货单才影响应收/应付与库存；
- 退货数量不能超过原订单数量，也不能超过原订单尚未退货的部分；
- 销售退货默认入退货仓（无退货仓时入默认仓），采购退货从库存中扣减。
"""
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Product,
    PurchaseOrder,
    PurchaseReturn,
    PurchaseReturnItem,
    SalesOrder,
    SalesReturn,
    SalesReturnItem,
    Warehouse,
)
from .stock import apply_stock_change, deduct_stock, resolve_target

# 会占用「可退数量」的退货状态：草稿已占用额度，避免同一订单重复建单后才在确认时报错
RESERVED_STATUS = ("draft", "confirmed")
CONFIRMED_STATUS = ("confirmed",)


def _sum_by(db: Session, model, statuses, group_col, **filters):
    stmt = select(group_col, func.sum(model.total_amount)).where(model.status.in_(statuses))
    for key, value in filters.items():
        stmt = stmt.where(getattr(model, key) == value)
    return {int(k): round(float(v or 0), 2) for k, v in db.execute(stmt.group_by(group_col)).all()}


def sales_returned_amount_by_customer(db: Session) -> dict[int, float]:
    return _sum_by(db, SalesReturn, CONFIRMED_STATUS, SalesReturn.customer_id)


def sales_returned_amount_by_order(db: Session, order_id: int | None = None) -> dict[int, float]:
    filters = {"order_id": order_id} if order_id else {}
    return _sum_by(db, SalesReturn, CONFIRMED_STATUS, SalesReturn.order_id, **filters)


def purchase_returned_amount_by_supplier(db: Session) -> dict[int, float]:
    return _sum_by(db, PurchaseReturn, CONFIRMED_STATUS, PurchaseReturn.supplier_id)


def purchase_returned_amount_by_order(db: Session, order_id: int | None = None) -> dict[int, float]:
    filters = {"order_id": order_id} if order_id else {}
    return _sum_by(db, PurchaseReturn, CONFIRMED_STATUS, PurchaseReturn.order_id, **filters)


def _returned_qty(db: Session, item_model, header_model, order_id: int, statuses) -> dict[int, int]:
    rows = db.execute(
        select(item_model.product_id, func.sum(item_model.quantity))
        .join(header_model, header_model.id == item_model.return_id)
        .where(header_model.order_id == order_id)
        .where(header_model.status.in_(statuses))
        .group_by(item_model.product_id)
    ).all()
    return {int(pid): int(qty or 0) for pid, qty in rows}


def sales_returned_qty(db: Session, order_id: int, statuses=RESERVED_STATUS) -> dict[int, int]:
    return _returned_qty(db, SalesReturnItem, SalesReturn, order_id, statuses)


def purchase_returned_qty(db: Session, order_id: int, statuses=RESERVED_STATUS) -> dict[int, int]:
    return _returned_qty(db, PurchaseReturnItem, PurchaseReturn, order_id, statuses)


# ---------------- 仓库 ----------------
def return_warehouse(db: Session, preferred_id: int | None) -> Warehouse | None:
    """销售退货优先入「退货仓」（code=RTN），找不到则交给默认仓逻辑。"""
    if preferred_id:
        warehouse = db.get(Warehouse, preferred_id)
        if not warehouse or not warehouse.is_active:
            raise HTTPException(status_code=400, detail="退货仓库不存在或已停用")
        return warehouse
    return db.scalar(
        select(Warehouse).where(Warehouse.code == "RTN").where(Warehouse.is_active == True)  # noqa: E712
    )


# ---------------- 序列化 ----------------
def serialize_return(ret: SalesReturn | PurchaseReturn, kind: str) -> dict:
    is_sales = kind == "sales"
    partner = ret.customer if is_sales else ret.supplier
    return {
        "id": ret.id,
        "return_no": ret.return_no,
        "order_id": ret.order_id,
        "order_no": ret.order.order_no if ret.order else "",
        "partner_id": partner.id if partner else 0,
        "partner_name": partner.name if partner else "",
        "warehouse_id": ret.warehouse_id,
        "warehouse_name": ret.warehouse.name if ret.warehouse else "",
        "status": ret.status,
        "total_amount": round(float(ret.total_amount or 0), 2),
        "reason": ret.reason or "",
        "created_by": ret.created_by,
        "created_at": ret.created_at,
        "confirmed_at": ret.confirmed_at,
        "approval_required": int(ret.approval_required or 0),
        "approval_approved": int(ret.approval_approved or 0),
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product.name if item.product else "",
                "product_sku": item.product.sku if item.product else "",
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "amount": item.amount,
            }
            for item in ret.items
        ],
    }


# ---------------- 可退明细 ----------------
def sales_returnable_items(db: Session, order: SalesOrder) -> list[dict]:
    """可退明细：订单数量扣除已退，出库模式下还要受「已发货数量」限制。

    货没发出就不存在退货，否则会凭空回补库存。
    """
    from ..config import settings
    from . import outbound as outbound_service

    returned = sales_returned_qty(db, order.id)
    shipped = (
        outbound_service.shipped_by_product(db, order.id)
        if settings.SALES_SHIPPING_MODE == "outbound"
        else None
    )
    rows = []
    for item in order.items:
        done = returned.get(item.product_id, 0)
        remaining = max(item.quantity - done, 0)
        if shipped is not None:
            remaining = min(remaining, max(shipped.get(item.product_id, 0) - done, 0))
        rows.append(
            {
                "product_id": item.product_id,
                "product_name": item.product.name if item.product else "",
                "product_sku": item.product.sku if item.product else "",
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "returned_qty": done,
                "remaining_qty": remaining,
            }
        )
    return rows


def purchase_returnable_items(db: Session, order: PurchaseOrder) -> list[dict]:
    returned = purchase_returned_qty(db, order.id)
    return [
        {
            "product_id": item.product_id,
            "product_name": item.product.name if item.product else "",
            "product_sku": item.product.sku if item.product else "",
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "returned_qty": returned.get(item.product_id, 0),
            "remaining_qty": max(item.quantity - returned.get(item.product_id, 0), 0),
        }
        for item in order.items
    ]


# ---------------- 建单 ----------------
def build_sales_return(
    db: Session, payload, user, order: SalesOrder, warehouse_id: int | None
) -> SalesReturn:
    if order.status not in ("confirmed", "completed"):
        raise HTTPException(status_code=400, detail="只有已确认/已完成的销售订单可以退货")
    if own_order_blocked(db, order, user):
        raise HTTPException(status_code=403, detail="数据范围限制：只能操作本人负责的订单")

    warehouse = return_warehouse(db, warehouse_id)
    ret = SalesReturn(
        return_no=_next_no(db, SalesReturn, "SR"),
        order_id=order.id,
        customer_id=order.customer_id,
        warehouse_id=warehouse.id if warehouse else None,
        location_id=payload.location_id,
        status="draft",
        reason=payload.reason or "",
        created_by=user.username,
    )
    price_map = {item.product_id: float(item.unit_price) for item in order.items}
    total = 0.0
    for item in payload.items:
        if item.product_id not in price_map:
            raise HTTPException(status_code=400, detail=f"商品 {item.product_id} 不在原订单中")
        product = db.get(Product, item.product_id)
        if not product:
            raise HTTPException(status_code=400, detail=f"商品 {item.product_id} 不存在")
        unit_price = price_map[item.product_id]
        amount = round(item.quantity * unit_price, 2)
        total += amount
        ret.items.append(
            SalesReturnItem(
                product_id=item.product_id,
                quantity=item.quantity,
                unit_price=unit_price,
                amount=amount,
            )
        )
    ret.total_amount = round(total, 2)
    db.add(ret)
    db.flush()
    return ret


def build_purchase_return(
    db: Session, payload, user, order: PurchaseOrder, warehouse_id: int | None
) -> PurchaseReturn:
    if order.status != "received":
        raise HTTPException(status_code=400, detail="只有已入库的采购订单可以退货")
    if own_order_blocked(db, order, user):
        raise HTTPException(status_code=403, detail="数据范围限制：只能操作本人负责的订单")

    warehouse = return_warehouse(db, warehouse_id)
    ret = PurchaseReturn(
        return_no=_next_no(db, PurchaseReturn, "PR"),
        order_id=order.id,
        supplier_id=order.supplier_id,
        warehouse_id=warehouse.id if warehouse else None,
        status="draft",
        reason=payload.reason or "",
        created_by=user.username,
    )
    price_map = {item.product_id: float(item.unit_price) for item in order.items}
    total = 0.0
    for item in payload.items:
        if item.product_id not in price_map:
            raise HTTPException(status_code=400, detail=f"商品 {item.product_id} 不在原采购单中")
        product = db.get(Product, item.product_id)
        if not product:
            raise HTTPException(status_code=400, detail=f"商品 {item.product_id} 不存在")
        unit_price = price_map[item.product_id]
        amount = round(item.quantity * unit_price, 2)
        total += amount
        ret.items.append(
            PurchaseReturnItem(
                product_id=item.product_id,
                quantity=item.quantity,
                unit_price=unit_price,
                amount=amount,
            )
        )
    ret.total_amount = round(total, 2)
    db.add(ret)
    db.flush()
    return ret


def _next_no(db: Session, model, prefix: str) -> str:
    from ..utils import gen_order_no

    return gen_order_no(db, model, prefix, field="return_no")


def own_order_blocked(db: Session, order, user) -> bool:
    from ..scoping import own_scope

    return own_scope(user.role) and (order.created_by or "") != user.username


# ---------------- 确认 / 取消 ----------------
def confirm_sales_return(db: Session, ret: SalesReturn, user) -> None:
    if ret.status != "draft":
        raise HTTPException(status_code=400, detail="只有草稿退货单可以确认")

    order = db.get(SalesOrder, ret.order_id)
    if not order or order.status not in ("confirmed", "completed"):
        raise HTTPException(status_code=400, detail="原销售订单状态不支持退货")

    confirmed_qty = sales_returned_qty(db, order.id, CONFIRMED_STATUS)
    order_qty = {item.product_id: item.quantity for item in order.items}
    shortages = []
    for item in ret.items:
        available = order_qty.get(item.product_id, 0) - confirmed_qty.get(item.product_id, 0)
        if item.quantity > available:
            shortages.append(
                f"{item.product.name if item.product else item.product_id}"
                f"（可退 {max(available, 0)}，本次 {item.quantity}）"
            )
    if shortages:
        raise HTTPException(status_code=400, detail="退货数量超过可退数量：" + "；".join(shortages))

    for item in ret.items:
        apply_stock_change(
            db,
            item.product,
            "in",
            item.quantity,
            warehouse_id=ret.warehouse_id,
            location_id=ret.location_id,
            reason=f"销售退货入库 {ret.return_no}（原单 {order.order_no}）",
            biz_type="销售退货入库",
            ref_no=ret.return_no,
            username=user.username,
        )
    ret.status = "confirmed"
    ret.confirmed_at = datetime.now()


def cancel_sales_return(db: Session, ret: SalesReturn, user) -> None:
    if ret.status == "cancelled":
        raise HTTPException(status_code=400, detail="退货单已取消")
    if ret.status == "confirmed":
        order = db.get(SalesOrder, ret.order_id)
        for item in ret.items:
            deduct_stock(
                db,
                item.product,
                item.quantity,
                reason=f"销售退货取消回滚 {ret.return_no}",
                biz_type="销售退货取消",
                ref_no=ret.return_no,
                username=user.username,
            )
        ret.cancelled_at = datetime.now()
    ret.status = "cancelled"


def confirm_purchase_return(db: Session, ret: PurchaseReturn, user) -> None:
    if ret.status != "draft":
        raise HTTPException(status_code=400, detail="只有草稿退货单可以确认")

    order = db.get(PurchaseOrder, ret.order_id)
    if not order or order.status != "received":
        raise HTTPException(status_code=400, detail="原采购订单尚未入库，无法退货")

    confirmed_qty = purchase_returned_qty(db, order.id, CONFIRMED_STATUS)
    order_qty = {item.product_id: item.quantity for item in order.items}
    shortages = []
    for item in ret.items:
        available = order_qty.get(item.product_id, 0) - confirmed_qty.get(item.product_id, 0)
        if item.quantity > available:
            shortages.append(
                f"{item.product.name if item.product else item.product_id}"
                f"（可退 {max(available, 0)}，本次 {item.quantity}）"
            )
    if shortages:
        raise HTTPException(status_code=400, detail="退货数量超过可退数量：" + "；".join(shortages))

    for item in ret.items:
        deduct_stock(
            db,
            item.product,
            item.quantity,
            reason=f"采购退货出库 {ret.return_no}（原单 {order.order_no}）",
            biz_type="采购退货出库",
            ref_no=ret.return_no,
            username=user.username,
        )
    ret.status = "confirmed"
    ret.confirmed_at = datetime.now()


def cancel_purchase_return(db: Session, ret: PurchaseReturn, user) -> None:
    if ret.status == "cancelled":
        raise HTTPException(status_code=400, detail="退货单已取消")
    if ret.status == "confirmed":
        for item in ret.items:
            apply_stock_change(
                db,
                item.product,
                "in",
                item.quantity,
                warehouse_id=ret.warehouse_id,
                reason=f"采购退货取消回库 {ret.return_no}",
                biz_type="采购退货取消",
                ref_no=ret.return_no,
                username=user.username,
            )
        ret.cancelled_at = datetime.now()
    ret.status = "cancelled"
