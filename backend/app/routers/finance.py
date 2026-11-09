"""财务模块：营收成本、毛利与应收分析。"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_perm
from ..models import Customer, Product, SalesOrder, SalesOrderItem
from ..services import analytics
from ..services import payables as ap_service
from ..services import receivables as ar_service

router = APIRouter(prefix="/api/finance", tags=["财务"], dependencies=[Depends(require_perm("finance:view"))])


def _cost_of_orders(db: Session, start: str, end: str | None = None) -> float:
    stmt = (
        select(func.coalesce(func.sum(SalesOrderItem.quantity * Product.cost_price), 0))
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
        .join(Product, Product.id == SalesOrderItem.product_id)
        .where(SalesOrder.status.in_(analytics.VALID_SALES_STATUS))
        .where(func.date(SalesOrder.order_date) >= start)
    )
    if end:
        stmt = stmt.where(func.date(SalesOrder.order_date) <= end)
    return round(float(db.scalar(stmt) or 0), 2)


@router.get("/overview", summary="财务概览")
def overview(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    start = (datetime.now() - timedelta(days=days - 1)).date().isoformat()
    revenue = db.scalar(
        select(func.coalesce(func.sum(SalesOrder.total_amount), 0))
        .where(SalesOrder.status.in_(analytics.VALID_SALES_STATUS))
        .where(func.date(SalesOrder.order_date) >= start)
    ) or 0
    revenue = round(float(revenue), 2)
    cost = _cost_of_orders(db, start)
    profit = round(revenue - cost, 2)
    margin = round(profit / revenue * 100, 1) if revenue else 0.0

    order_count = db.scalar(
        select(func.count()).select_from(SalesOrder)
        .where(SalesOrder.status.in_(analytics.VALID_SALES_STATUS))
        .where(func.date(SalesOrder.order_date) >= start)
    ) or 0

    # 真实应收 = 已确认订单 − 已确认退货 − 已核销收款
    receivable = ar_service.receivable_total(db)
    # 真实应付 = 已入库采购 − 已确认采购退货 − 已付款
    payable = ap_service.payable_total(db)

    inventory_value = analytics.inventory_value(db)
    return {
        "period_days": days,
        "revenue": revenue,
        "cost": cost,
        "gross_profit": profit,
        "gross_margin": margin,
        "order_count": int(order_count),
        "avg_order_value": round(revenue / order_count, 2) if order_count else 0.0,
        "receivable": round(float(receivable), 2),
        "payable": round(float(payable), 2),
        "inventory_value": inventory_value,
        "inventory_turnover_days": (
            round(inventory_value / (cost / days), 1) if cost else 0.0
        ),
    }


@router.get("/trend", summary="收入成本利润趋势")
def trend(days: int = Query(30, ge=7, le=90), db: Session = Depends(get_db)):
    start = (datetime.now() - timedelta(days=days - 1)).date()
    revenue_rows = db.execute(
        select(func.date(SalesOrder.order_date).label("day"), func.sum(SalesOrder.total_amount))
        .where(SalesOrder.status.in_(analytics.VALID_SALES_STATUS))
        .where(func.date(SalesOrder.order_date) >= start.isoformat())
        .group_by(func.date(SalesOrder.order_date))
    ).all()
    cost_rows = db.execute(
        select(
            func.date(SalesOrder.order_date).label("day"),
            func.sum(SalesOrderItem.quantity * Product.cost_price),
        )
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
        .join(Product, Product.id == SalesOrderItem.product_id)
        .where(SalesOrder.status.in_(analytics.VALID_SALES_STATUS))
        .where(func.date(SalesOrder.order_date) >= start.isoformat())
        .group_by(func.date(SalesOrder.order_date))
    ).all()
    revenue_map = {str(d): float(v or 0) for d, v in revenue_rows}
    cost_map = {str(d): float(v or 0) for d, v in cost_rows}

    result = []
    for i in range(days):
        day = (start + timedelta(days=i)).isoformat()
        revenue = round(revenue_map.get(day, 0.0), 2)
        cost = round(cost_map.get(day, 0.0), 2)
        result.append({"date": day, "revenue": revenue, "cost": cost, "profit": round(revenue - cost, 2)})
    return result


@router.get("/receivables", summary="应收账龄分析")
def receivables(db: Session = Depends(get_db)):
    """真实应收：已确认订单 − 已核销收款，口径与「应收与对账」页面一致。"""
    items = ar_service.customer_receivables(db)
    buckets = {"0-30天": 0.0, "31-60天": 0.0, "60天以上": 0.0}
    for item in items:
        buckets[item["bucket"]] = round(buckets[item["bucket"]] + item["balance"], 2)
    return {
        "total": round(sum(buckets.values()), 2),
        "buckets": [{"name": key, "value": value} for key, value in buckets.items()],
        "items": [
            {
                "customer_id": item["customer_id"],
                "customer_name": item["customer"],
                "level": item["level"],
                "amount": item["balance"],
                "orders": item["orders"],
                "age_days": item["age_days"],
                "bucket": item["bucket"],
            }
            for item in items
        ],
    }
