from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_perm
from ..models import Customer, Product, PurchaseOrder, SalesOrder, User
from ..scoping import scope_owner
from ..services import analytics
from ..utils import serialize_sales_order

router = APIRouter(prefix="/api/dashboard", tags=["看板"], dependencies=[Depends(require_perm("dashboard:view"))])


@router.get("/summary", summary="核心经营指标")
def summary(db: Session = Depends(get_db), user: User = Depends(require_perm("dashboard:view"))):
    today = datetime.now().date()
    month_start = today.replace(day=1)
    owner = scope_owner(user)  # 仅本人数据范围时只统计自己

    def sales_sum(start_date, end_date=None):
        stmt = select(func.coalesce(func.sum(SalesOrder.total_amount), 0)).where(
            SalesOrder.status.in_(analytics.VALID_SALES_STATUS)
        ).where(func.date(SalesOrder.order_date) >= start_date.isoformat())
        if end_date:
            stmt = stmt.where(func.date(SalesOrder.order_date) <= end_date.isoformat())
        if owner:
            stmt = stmt.where(SalesOrder.created_by == owner)
        return round(float(db.scalar(stmt) or 0), 2)

    today_sales = sales_sum(today)
    yesterday_sales = sales_sum(today - timedelta(days=1), today - timedelta(days=1))
    month_sales = sales_sum(month_start)

    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)
    prev_month_sales = sales_sum(prev_month_start, prev_month_end)

    orders_stmt = (
        select(func.count()).select_from(SalesOrder)
        .where(func.date(SalesOrder.order_date) == today.isoformat())
        .where(SalesOrder.status.in_(analytics.VALID_SALES_STATUS))
    )
    if owner:
        orders_stmt = orders_stmt.where(SalesOrder.created_by == owner)
    today_orders = db.scalar(orders_stmt) or 0

    low_stock = len(analytics.low_stock_products(db))
    product_count = db.scalar(select(func.count()).select_from(Product).where(Product.is_active == True)) or 0
    customer_stmt = select(func.count()).select_from(Customer)
    if owner:
        customer_stmt = customer_stmt.where(Customer.owner == owner)
    customer_count = db.scalar(customer_stmt) or 0
    pending_stmt = select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.status == "ordered")
    if owner:
        pending_stmt = pending_stmt.where(PurchaseOrder.created_by == owner)
    pending_purchase_count = db.scalar(pending_stmt) or 0
    draft_stmt = select(func.count()).select_from(SalesOrder).where(SalesOrder.status == "draft")
    if owner:
        draft_stmt = draft_stmt.where(SalesOrder.created_by == owner)
    draft_sales_count = db.scalar(draft_stmt) or 0

    def growth(current, previous):
        if previous <= 0:
            return 0.0
        return round((current - previous) / previous * 100, 1)

    return {
        "today_sales": today_sales,
        "yesterday_sales": yesterday_sales,
        "today_growth": growth(today_sales, yesterday_sales),
        "month_sales": month_sales,
        "month_growth": growth(month_sales, prev_month_sales),
        "today_orders": today_orders,
        "inventory_value": analytics.inventory_value(db),
        "low_stock_count": low_stock,
        "product_count": product_count,
        "customer_count": customer_count,
        "pending_purchase_count": pending_purchase_count,
        "draft_sales_count": draft_sales_count,
    }


@router.get("/trend", summary="销售趋势")
def trend(
    days: int = Query(30, ge=7, le=90),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("dashboard:view")),
):
    return analytics.daily_sales(db, days, scope_owner(user))


@router.get("/top-products", summary="热销商品")
def top_products(
    limit: int = Query(8, ge=1, le=20),
    days: int = Query(30),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("dashboard:view")),
):
    return analytics.product_sales(db, days, scope_owner(user))[:limit]


@router.get("/category-distribution", summary="分类销售分布")
def category_distribution(
    days: int = Query(30),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("dashboard:view")),
):
    rows = analytics.product_sales(db, days, scope_owner(user))
    agg: dict[str, float] = {}
    for row in rows:
        agg[row["category"]] = agg.get(row["category"], 0) + row["revenue"]
    return [{"name": k, "value": round(v, 2)} for k, v in agg.items()]


@router.get("/recent-orders", summary="最新订单")
def recent_orders(
    limit: int = Query(6, ge=1, le=20),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("dashboard:view")),
):
    stmt = select(SalesOrder)
    owner = scope_owner(user)
    if owner:
        stmt = stmt.where(SalesOrder.created_by == owner)
    rows = db.scalars(stmt.order_by(SalesOrder.id.desc()).limit(limit)).all()
    return [serialize_sales_order(o) for o in rows]
