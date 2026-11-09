"""经营数据统计，供看板和 AI 服务复用。"""
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Customer, Product, PurchaseOrder, SalesOrder, SalesOrderItem

VALID_SALES_STATUS = ("confirmed", "completed")


def daily_sales(db: Session, days: int = 30, username: str | None = None) -> list[dict]:
    """按天统计销售额与订单数；username 非空时只统计该用户的订单（数据范围隔离）。"""
    start = (datetime.now() - timedelta(days=days - 1)).date()
    stmt = (
        select(
            func.date(SalesOrder.order_date).label("day"),
            func.sum(SalesOrder.total_amount).label("amount"),
            func.count(SalesOrder.id).label("orders"),
        )
        .where(SalesOrder.status.in_(VALID_SALES_STATUS))
        .where(func.date(SalesOrder.order_date) >= start.isoformat())
    )
    if username:
        stmt = stmt.where(SalesOrder.created_by == username)
    rows = db.execute(
        stmt.group_by(func.date(SalesOrder.order_date)).order_by(func.date(SalesOrder.order_date))
    ).all()

    by_day = {str(r.day): (float(r.amount or 0), int(r.orders or 0)) for r in rows}
    result = []
    for i in range(days):
        d = start + timedelta(days=i)
        amount, orders = by_day.get(d.isoformat(), (0.0, 0))
        result.append({"date": d.isoformat(), "amount": round(amount, 2), "orders": orders})
    return result


def product_sales(db: Session, days: int = 30, username: str | None = None) -> list[dict]:
    """按商品统计销量与销售额；username 非空时只统计该用户的订单。"""
    start = (datetime.now() - timedelta(days=days - 1)).date()
    stmt = (
        select(
            Product.id,
            Product.name,
            Product.sku,
            Product.category,
            Product.stock_qty,
            Product.safety_stock,
            Product.cost_price,
            Product.sale_price,
            func.sum(SalesOrderItem.quantity).label("qty"),
            func.sum(SalesOrderItem.amount).label("revenue"),
        )
        .join(SalesOrderItem, SalesOrderItem.product_id == Product.id)
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
        .where(SalesOrder.status.in_(VALID_SALES_STATUS))
        .where(func.date(SalesOrder.order_date) >= start.isoformat())
    )
    if username:
        stmt = stmt.where(SalesOrder.created_by == username)
    rows = db.execute(
        stmt.group_by(Product.id).order_by(func.sum(SalesOrderItem.amount).desc())
    ).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "sku": r.sku,
            "category": r.category,
            "stock_qty": r.stock_qty,
            "safety_stock": r.safety_stock,
            "cost_price": r.cost_price,
            "sale_price": r.sale_price,
            "qty": int(r.qty or 0),
            "revenue": round(float(r.revenue or 0), 2),
        }
        for r in rows
    ]


def low_stock_products(db: Session) -> list[Product]:
    return list(
        db.scalars(
            select(Product)
            .where(Product.is_active == True)  # noqa: E712
            .where(Product.stock_qty <= Product.safety_stock)
            .order_by((Product.stock_qty - Product.safety_stock))
        ).all()
    )


def inventory_value(db: Session) -> float:
    value = db.scalar(select(func.sum(Product.cost_price * Product.stock_qty)))
    return round(float(value or 0), 2)


def customer_stats(db: Session, days: int = 30, username: str | None = None) -> list[dict]:
    """客户贡献度：近 N 天销售额、订单数、最近下单时间；username 非空时只看本人客户与订单。"""
    start = (datetime.now() - timedelta(days=days - 1)).date()
    stmt = (
        select(
            Customer.id,
            Customer.name,
            Customer.level,
            func.sum(SalesOrder.total_amount).label("amount"),
            func.count(SalesOrder.id).label("orders"),
            func.max(SalesOrder.order_date).label("last_order"),
        )
        .join(SalesOrder, SalesOrder.customer_id == Customer.id)
        .where(SalesOrder.status.in_(VALID_SALES_STATUS))
    )
    if username:
        stmt = stmt.where(SalesOrder.created_by == username)
    rows = db.execute(
        stmt.group_by(Customer.id).order_by(func.sum(SalesOrder.total_amount).desc())
    ).all()
    last_rows = {r.id: (r.amount, r.orders, r.last_order) for r in rows}
    customer_stmt = select(Customer)
    if username:
        customer_stmt = customer_stmt.where(Customer.owner == username)
    all_customers = db.scalars(customer_stmt).all()
    stats = []
    for c in all_customers:
        amount, orders, last_order = last_rows.get(c.id, (0, 0, None))
        stats.append(
            {
                "id": c.id,
                "name": c.name,
                "level": c.level,
                "amount": round(float(amount or 0), 2),
                "orders": int(orders or 0),
                "last_order": last_order.strftime("%Y-%m-%d") if last_order else "",
                "days_since_last": (date.today() - last_order.date()).days if last_order else None,
            }
        )
    stats.sort(key=lambda x: x["amount"], reverse=True)
    return stats


def pending_purchase(db: Session) -> list[dict]:
    rows = db.scalars(select(PurchaseOrder).where(PurchaseOrder.status == "ordered")).all()
    return [
        {
            "id": o.id,
            "order_no": o.order_no,
            "supplier": o.supplier.name if o.supplier else "",
            "total_amount": o.total_amount,
            "order_date": o.order_date.strftime("%Y-%m-%d"),
        }
        for o in rows
    ]
