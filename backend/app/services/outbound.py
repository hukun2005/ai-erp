"""销售出库（发货）服务。

一张销售订单可以拆成多张出库单，按实际发货分批发货：
- 建单：按订单剩余未发数量生成草稿；
- 确认发货：逐行扣减库存、写库存流水，出库单转为 shipped；
- 签收：只登记交付凭证，不动库存；
- 取消：已发货的出库单取消时把货退回默认库位，草稿则直接删除。
"""
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Customer, OutboundItem, OutboundOrder, Product, SalesOrder, SalesOrderItem
from . import reservation
from .stock import apply_stock_change, deduct_stock, resolve_target

STATUS_TEXT = {
    "draft": "待发货",
    "pending": "待审批",
    "shipped": "已出库",
    "signed": "已签收",
    "cancelled": "已取消",
}

# 已占用（不可再发）的状态：草稿不占数量，取消的释放
OCCUPYING_STATUSES = ("draft", "shipped", "signed")


def shipped_quantities(db: Session, order_id: int) -> dict[int, int]:
    """按订单明细行汇总已发出的数量（不含已取消的单）。"""
    rows = db.execute(
        select(OutboundItem.order_item_id, func.sum(OutboundItem.quantity))
        .join(OutboundOrder, OutboundOrder.id == OutboundItem.outbound_id)
        .where(OutboundOrder.sales_order_id == order_id)
        .where(OutboundOrder.status.in_(("shipped", "signed")))
        .group_by(OutboundItem.order_item_id)
    ).all()
    return {row[0]: int(row[1] or 0) for row in rows if row[0] is not None}


def shipped_by_product(db: Session, order_id: int) -> dict[int, int]:
    """按商品汇总已发出的数量（已出库 + 已签收），用于限制可退数量。"""
    rows = db.execute(
        select(OutboundItem.product_id, func.sum(OutboundItem.quantity))
        .join(OutboundOrder, OutboundOrder.id == OutboundItem.outbound_id)
        .where(OutboundOrder.sales_order_id == order_id)
        .where(OutboundOrder.status.in_(("shipped", "signed")))
        .group_by(OutboundItem.product_id)
    ).all()
    return {row[0]: int(row[1] or 0) for row in rows if row[0] is not None}


def reserved_quantities(db: Session, order_id: int) -> dict[int, int]:
    """按订单明细行汇总「在建出库单但还没发货」的数量，避免重复建单超发。"""
    rows = db.execute(
        select(OutboundItem.order_item_id, func.sum(OutboundItem.quantity))
        .join(OutboundOrder, OutboundOrder.id == OutboundItem.outbound_id)
        .where(OutboundOrder.sales_order_id == order_id)
        .where(OutboundOrder.status == "draft")
        .group_by(OutboundItem.order_item_id)
    ).all()
    return {row[0]: int(row[1] or 0) for row in rows if row[0] is not None}


def item_progress(db: Session, order: SalesOrder) -> list[dict]:
    """每行的订单数量、已发、在途与未发数量。"""
    shipped = shipped_quantities(db, order.id)
    reserved = reserved_quantities(db, order.id)
    rows = []
    for item in order.items:
        done = shipped.get(item.id, 0)
        pending = reserved.get(item.id, 0)
        rows.append(
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product.name if item.product else "",
                "quantity": item.quantity,
                "shipped_quantity": done,
                "reserved_quantity": pending,
                "unshipped_quantity": max(item.quantity - done - pending, 0),
            }
        )
    return rows


def order_progress(db: Session, order: SalesOrder) -> dict:
    """订单整体发货进度，供列表/详情展示。"""
    rows = item_progress(db, order)
    total = sum(row["quantity"] for row in rows)
    shipped = sum(row["shipped_quantity"] for row in rows)
    return {
        "total_quantity": total,
        "shipped_quantity": shipped,
        "unshipped_quantity": sum(row["unshipped_quantity"] for row in rows),
        "progress_percent": round(shipped / total * 100, 1) if total else 0.0,
        "items": rows,
    }


def _remaining_map(db: Session, order: SalesOrder) -> dict[int, int]:
    """订单每一行还能发多少（已发 + 在途都算占用）。"""
    shipped = shipped_quantities(db, order.id)
    reserved = reserved_quantities(db, order.id)
    return {
        item.id: max(item.quantity - shipped.get(item.id, 0) - reserved.get(item.id, 0), 0)
        for item in order.items
    }


def remaining_for_order(db: Session, order: SalesOrder) -> dict[int, int]:
    """供扫码累加数量时校验上限：按订单明细行返回剩余可发量。"""
    return _remaining_map(db, order)


def create_outbound(
    db: Session,
    order: SalesOrder,
    *,
    user,
    items: list[dict] | None = None,
    warehouse_id: int | None = None,
    logistics_company: str = "",
    logistics_no: str = "",
    remark: str = "",
) -> OutboundOrder:
    """按订单剩余未发数量建出库单；items 为空表示「全部剩余」。

    items 形如 [{"order_item_id": 1, "quantity": 2}]，数量不得超过剩余可发量。
    """
    if order.status not in ("confirmed", "completed"):
        raise HTTPException(status_code=400, detail="只有已确认的销售订单可以生成出库单")
    remaining = _remaining_map(db, order)
    if not any(remaining.values()):
        raise HTTPException(status_code=400, detail="该订单已经没有待发数量")

    plan: list[tuple[SalesOrderItem, int]] = []
    if items:
        by_id = {item.id: item for item in order.items}
        for row in items:
            order_item_id = int(row["order_item_id"])
            item = by_id.get(order_item_id)
            if item is None:
                raise HTTPException(status_code=400, detail=f"明细行 {order_item_id} 不属于该订单")
            quantity = int(row["quantity"])
            if quantity <= 0:
                raise HTTPException(status_code=400, detail="发货数量必须大于 0")
            if quantity > remaining[order_item_id]:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"「{item.product.name if item.product else order_item_id}」可发数量 "
                        f"{remaining[order_item_id]}，本次填写 {quantity}"
                    ),
                )
            plan.append((item, quantity))
    else:
        plan = [(item, remaining[item.id]) for item in order.items if remaining[item.id] > 0]

    warehouse, location = resolve_target(db, warehouse_id, None)
    outbound = OutboundOrder(
        outbound_no=_gen_outbound_no(db),
        sales_order_id=order.id,
        customer_id=order.customer_id,
        warehouse_id=warehouse.id,
        location_id=location.id,
        status="draft",
        logistics_company=logistics_company.strip(),
        logistics_no=logistics_no.strip(),
        remark=remark or "",
        created_by=user.username,
        created_at=datetime.now(),
    )
    total_quantity = 0
    total_amount = 0.0
    for item, quantity in plan:
        amount = round(quantity * float(item.unit_price or 0), 2)
        total_quantity += quantity
        total_amount += amount
        outbound.items.append(
            OutboundItem(
                order_item_id=item.id,
                product_id=item.product_id,
                quantity=quantity,
                unit_price=item.unit_price,
                amount=amount,
            )
        )
    outbound.total_quantity = total_quantity
    outbound.total_amount = round(total_amount, 2)
    db.add(outbound)
    db.flush()
    return outbound


def _gen_outbound_no(db: Session) -> str:
    head = f"OB{datetime.now().strftime('%Y%m%d')}"
    values = db.scalars(
        select(OutboundOrder.outbound_no).where(OutboundOrder.outbound_no.like(f"{head}%"))
    ).all()
    max_seq = 0
    for value in values:
        suffix = str(value or "")[len(head):]
        if suffix.isdigit():
            max_seq = max(max_seq, int(suffix))
    return f"{head}{max_seq + 1:04d}"


def ship(db: Session, outbound: OutboundOrder, user) -> OutboundOrder:
    """确认发货：逐行扣库存并写流水。库存不足时整单失败，不会出现半单。"""
    if outbound.status != "draft":
        raise HTTPException(status_code=400, detail=f"当前状态为「{STATUS_TEXT.get(outbound.status, outbound.status)}」，不能重复发货")
    shortages = []
    for item in outbound.items:
        product = db.get(Product, item.product_id)
        if product is None:
            raise HTTPException(status_code=400, detail=f"商品 {item.product_id} 不存在")
        if product.stock_qty < item.quantity:
            shortages.append(f"{product.name}（需 {item.quantity}，库存 {product.stock_qty}）")
    if shortages:
        raise HTTPException(status_code=400, detail="库存不足：" + "；".join(shortages))

    for item in outbound.items:
        product = db.get(Product, item.product_id)
        deduct_stock(
            db,
            product,
            item.quantity,
            reason=f"销售出库 {outbound.outbound_no}",
            biz_type="销售出库",
            ref_no=outbound.outbound_no,
            username=user.username,
        )
    # 发货即把订单占用转为已消耗，可用量随之释放
    quantities: dict[int, int] = {}
    for item in outbound.items:
        quantities[item.product_id] = quantities.get(item.product_id, 0) + int(item.quantity)
    reservation.consume_for_order(db, outbound.sales_order, quantities)
    outbound.status = "shipped"
    outbound.shipped_at = datetime.now()
    outbound.ship_date = outbound.ship_date or outbound.shipped_at
    db.flush()
    return outbound


def sign(db: Session, outbound: OutboundOrder, signed_by: str = "") -> OutboundOrder:
    """签收登记：只留交付凭证，不重复动库存。"""
    if outbound.status != "shipped":
        raise HTTPException(status_code=400, detail="只有已出库的单据可以登记签收")
    outbound.status = "signed"
    outbound.signed_at = datetime.now()
    outbound.signed_by = (signed_by or "").strip()[:50]
    db.flush()
    return outbound


def cancel(db: Session, outbound: OutboundOrder, user) -> OutboundOrder:
    """取消出库单：已发货的把货退回默认库位；草稿直接作废。"""
    if outbound.status == "cancelled":
        raise HTTPException(status_code=400, detail="出库单已取消")
    if outbound.status in ("shipped", "signed"):
        for item in outbound.items:
            product = db.get(Product, item.product_id)
            if product is None:
                continue
            apply_stock_change(
                db,
                product,
                "in",
                item.quantity,
                warehouse_id=outbound.warehouse_id,
                location_id=outbound.location_id,
                reason=f"销售出库 {outbound.outbound_no} 取消回库",
                biz_type="销售出库取消回库",
                ref_no=outbound.outbound_no,
                username=user.username,
            )
        # 货回到库里，订单仍有效时重新占用，避免这段库存被别的单抢走
        if outbound.sales_order is not None and outbound.sales_order.status in ("confirmed", "completed"):
            quantities: dict[int, int] = {}
            for item in outbound.items:
                quantities[item.product_id] = quantities.get(item.product_id, 0) + int(item.quantity)
            reservation.reserve_quantities(
                db, outbound.sales_order, quantities, user, check_available=False
            )
    outbound.status = "cancelled"
    outbound.cancelled_at = datetime.now()
    db.flush()
    return outbound


def serialize(outbound: OutboundOrder, *, with_items: bool = True) -> dict:
    order = outbound.sales_order
    customer = outbound.customer
    data = {
        "id": outbound.id,
        "outbound_no": outbound.outbound_no,
        "sales_order_id": outbound.sales_order_id,
        "sales_order_no": order.order_no if order else "",
        "customer_id": outbound.customer_id,
        "customer_name": customer.name if customer else "",
        "customer_address": customer.address if customer else "",
        "warehouse_id": outbound.warehouse_id,
        "warehouse_name": outbound.warehouse.name if outbound.warehouse else "",
        "location_id": outbound.location_id,
        "location_code": outbound.location.code if outbound.location else "",
        "status": outbound.status,
        "status_text": STATUS_TEXT.get(outbound.status, outbound.status),
        "logistics_company": outbound.logistics_company,
        "logistics_no": outbound.logistics_no,
        "ship_date": outbound.ship_date,
        "shipped_at": outbound.shipped_at,
        "signed_at": outbound.signed_at,
        "signed_by": outbound.signed_by,
        "cancelled_at": outbound.cancelled_at,
        "total_quantity": outbound.total_quantity,
        "total_amount": outbound.total_amount,
        "remark": outbound.remark,
        "created_by": outbound.created_by,
        "created_at": outbound.created_at,
        "approval_required": int(outbound.approval_required or 0),
        "approval_approved": int(outbound.approval_approved or 0),
    }
    if with_items:
        data["items"] = [
            {
                "id": item.id,
                "order_item_id": item.order_item_id,
                "product_id": item.product_id,
                "product_name": item.product.name if item.product else "",
                "product_sku": item.product.sku if item.product else "",
                "product_unit": item.product.unit if item.product else "",
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "amount": item.amount,
                "remark": item.remark,
            }
            for item in outbound.items
        ]
    return data


def customer_name(db: Session, customer_id: int) -> str:
    customer = db.get(Customer, customer_id)
    return customer.name if customer else ""
