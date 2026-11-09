"""销售订单的库存预留。

确认订单时按明细占用可用量（不扣库存），发货时把占用转成消耗并扣减库存，
订单取消则释放。可用量 = 商品总库存 − 活跃预留，用来避免超卖。
"""
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Product, SalesOrder, StockReservation

ACTIVE = "active"


def reserved_map(db: Session, product_ids: list[int] | None = None) -> dict[int, int]:
    """按商品汇总活跃预留数量。"""
    stmt = (
        select(StockReservation.product_id, func.sum(StockReservation.quantity))
        .where(StockReservation.status == ACTIVE)
        .group_by(StockReservation.product_id)
    )
    if product_ids:
        stmt = stmt.where(StockReservation.product_id.in_(product_ids))
    return {row[0]: int(row[1] or 0) for row in db.execute(stmt).all()}


def reserved_total(db: Session, product_id: int) -> int:
    return reserved_map(db, [product_id]).get(product_id, 0)


def available_qty(db: Session, product: Product, reserved: dict[int, int] | None = None) -> int:
    """可用量 = 现存量 − 活跃预留。"""
    if reserved is None:
        reserved = reserved_map(db, [product.id])
    return int(product.stock_qty or 0) - int(reserved.get(product.id, 0))


def _reserve_rows(db: Session, order: SalesOrder, quantities: dict[int, int], user) -> list[StockReservation]:
    rows = []
    for product_id, quantity in quantities.items():
        if quantity <= 0:
            continue
        row = StockReservation(
            product_id=product_id,
            order_type="sales",
            order_id=order.id,
            order_no=order.order_no,
            quantity=quantity,
            status=ACTIVE,
            created_by=getattr(user, "username", "") or "",
            created_at=datetime.now(),
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def reserve_quantities(
    db: Session,
    order: SalesOrder,
    quantities: dict[int, int],
    user,
    *,
    check_available: bool = True,
) -> list[StockReservation]:
    """按给定数量占用库存（发货单取消回库时也复用它）。"""
    if check_available:
        reserved = reserved_map(db, list(quantities))
        shortages = []
        for product_id, quantity in quantities.items():
            product = db.get(Product, product_id)
            if product is None:
                raise HTTPException(status_code=400, detail=f"商品 {product_id} 不存在")
            available = int(product.stock_qty or 0) - reserved.get(product_id, 0)
            if available < quantity:
                shortages.append(f"{product.name}（需 {quantity}，可用 {available}）")
        if shortages:
            raise HTTPException(status_code=400, detail="可用库存不足：" + "；".join(shortages))
    return _reserve_rows(db, order, quantities, user)


def reserve_for_order(db: Session, order: SalesOrder, user, *, check_available: bool = True) -> list[StockReservation]:
    """确认订单时占用库存；可用量不足整单拒绝，不产生半单预留。"""
    quantities: dict[int, int] = {}
    for item in order.items:
        quantities[item.product_id] = quantities.get(item.product_id, 0) + int(item.quantity)
    return reserve_quantities(db, order, quantities, user, check_available=check_available)


def release_for_order(db: Session, order: SalesOrder, status: str = "released") -> int:
    """订单取消时释放占用。"""
    rows = db.scalars(
        select(StockReservation)
        .where(StockReservation.order_id == order.id)
        .where(StockReservation.order_type == "sales")
        .where(StockReservation.status == ACTIVE)
    ).all()
    for row in rows:
        row.status = status
    db.flush()
    return len(rows)


def consume_for_order(db: Session, order: SalesOrder, quantities: dict[int, int]) -> int:
    """发货后把对应数量的占用转为已消耗（按创建顺序先进先出）。"""
    consumed = 0
    for product_id, quantity in quantities.items():
        remaining = int(quantity)
        if remaining <= 0:
            continue
        rows = db.scalars(
            select(StockReservation)
            .where(StockReservation.order_id == order.id)
            .where(StockReservation.product_id == product_id)
            .where(StockReservation.status == ACTIVE)
            .order_by(StockReservation.id)
        ).all()
        for row in rows:
            if remaining <= 0:
                break
            take = min(int(row.quantity), remaining)
            row.quantity = int(row.quantity) - take
            remaining -= take
            consumed += take
            if row.quantity == 0:
                row.status = "consumed"
        # 预留不足（例如历史订单没有预留记录）时不报错，只是没有可消耗的占用
    db.flush()
    return consumed
