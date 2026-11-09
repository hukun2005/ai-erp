from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_perm
from ..models import (
    Customer,
    Product,
    ProductionOrder,
    PurchaseOrder,
    PurchaseOrderItem,
    SalesOrder,
    SalesOrderItem,
    StockLog,
    Supplier,
    User,
)
from ..services import payables, receivables
from .production import serialize_order

router = APIRouter(prefix="/api/reports", tags=["报表中心"])


def _range(start: str, end: str) -> tuple[datetime, datetime, str, str]:
    today = datetime.now().date()
    if not end:
        end = today.strftime("%Y-%m-%d")
    if not start:
        start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=29)).strftime("%Y-%m-%d")
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
    return start_dt, end_dt, start, end


@router.get("/overview", summary="综合报表：单据、库存与账款概览")
def overview(
    start: str = Query(""),
    end: str = Query(""),
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("report:view")),
):
    start_dt, end_dt, start_s, end_s = _range(start, end)

    sales_count = db.scalar(
        select(func.count()).select_from(SalesOrder).where(
            SalesOrder.order_date >= start_dt,
            SalesOrder.order_date < end_dt,
            SalesOrder.status != "cancelled",
        )
    ) or 0
    sales_amount = db.scalar(
        select(func.coalesce(func.sum(SalesOrder.total_amount), 0)).where(
            SalesOrder.order_date >= start_dt,
            SalesOrder.order_date < end_dt,
            SalesOrder.status != "cancelled",
        )
    ) or 0

    purchase_count = db.scalar(
        select(func.count()).select_from(PurchaseOrder).where(
            PurchaseOrder.order_date >= start_dt,
            PurchaseOrder.order_date < end_dt,
            PurchaseOrder.status != "cancelled",
        )
    ) or 0
    purchase_amount = db.scalar(
        select(func.coalesce(func.sum(PurchaseOrder.total_amount), 0)).where(
            PurchaseOrder.order_date >= start_dt,
            PurchaseOrder.order_date < end_dt,
            PurchaseOrder.status != "cancelled",
        )
    ) or 0

    stock_in = db.scalar(
        select(func.coalesce(func.sum(StockLog.change_qty), 0)).where(
            StockLog.created_at >= start_dt,
            StockLog.created_at < end_dt,
            StockLog.change_qty > 0,
        )
    ) or 0
    stock_out = db.scalar(
        select(func.coalesce(func.sum(StockLog.change_qty), 0)).where(
            StockLog.created_at >= start_dt,
            StockLog.created_at < end_dt,
            StockLog.change_qty < 0,
        )
    ) or 0

    prod_count = db.scalar(
        select(func.count()).select_from(ProductionOrder).where(
            ProductionOrder.created_at >= start_dt, ProductionOrder.created_at < end_dt
        )
    ) or 0
    prod_qty = db.scalar(
        select(func.coalesce(func.sum(ProductionOrder.quantity), 0)).where(
            ProductionOrder.created_at >= start_dt, ProductionOrder.created_at < end_dt
        )
    ) or 0
    completed_qty = db.scalar(
        select(func.coalesce(func.sum(ProductionOrder.quantity), 0)).where(
            ProductionOrder.status == "completed",
            ProductionOrder.completed_at >= start_dt,
            ProductionOrder.completed_at < end_dt,
        )
    ) or 0

    inventory_value = db.scalar(
        select(func.coalesce(func.sum(Product.stock_qty * Product.cost_price), 0)).where(
            Product.is_active == True  # noqa: E712
        )
    ) or 0

    return {
        "range": {"start": start_s, "end": end_s},
        "sales": {"order_count": sales_count, "amount": round(sales_amount, 2)},
        "purchase": {"order_count": purchase_count, "amount": round(purchase_amount, 2)},
        "stock": {"in_qty": int(stock_in), "out_qty": int(abs(stock_out))},
        "production": {
            "order_count": prod_count,
            "plan_qty": int(prod_qty),
            "completed_qty": int(completed_qty),
        },
        "inventory_value": round(inventory_value, 2),
        "receivable_total": round(receivables.receivable_total(db), 2),
        "payable_total": round(payables.payable_total(db), 2),
    }


@router.get("/sales", summary="销售报表：按客户 / 商品统计")
def sales_report(
    start: str = Query(""),
    end: str = Query(""),
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("report:view")),
):
    start_dt, end_dt, start_s, end_s = _range(start, end)

    by_customer = db.execute(
        select(
            Customer.id,
            Customer.name,
            func.count(SalesOrder.id),
            func.coalesce(func.sum(SalesOrder.total_amount), 0),
        )
        .join(SalesOrder, SalesOrder.customer_id == Customer.id)
        .where(
            SalesOrder.order_date >= start_dt,
            SalesOrder.order_date < end_dt,
            SalesOrder.status != "cancelled",
        )
        .group_by(Customer.id, Customer.name)
        .order_by(func.sum(SalesOrder.total_amount).desc())
    ).all()

    by_product = db.execute(
        select(
            Product.id,
            Product.name,
            Product.sku,
            Product.unit,
            func.coalesce(func.sum(SalesOrderItem.quantity), 0),
            func.coalesce(func.sum(SalesOrderItem.amount), 0),
            func.coalesce(func.sum(SalesOrderItem.quantity * Product.cost_price), 0),
        )
        .join(SalesOrderItem, SalesOrderItem.product_id == Product.id)
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
        .where(
            SalesOrder.order_date >= start_dt,
            SalesOrder.order_date < end_dt,
            SalesOrder.status != "cancelled",
        )
        .group_by(Product.id, Product.name, Product.sku, Product.unit)
        .order_by(func.sum(SalesOrderItem.amount).desc())
    ).all()

    return {
        "range": {"start": start_s, "end": end_s},
        "by_customer": [
            {"customer_id": row[0], "customer_name": row[1], "order_count": row[2], "amount": round(row[3], 2)}
            for row in by_customer
        ],
        "by_product": [
            {
                "product_id": row[0],
                "product_name": row[1],
                "sku": row[2],
                "unit": row[3],
                "quantity": int(row[4]),
                "amount": round(row[5], 2),
                "cost": round(row[6], 2),
                "gross_profit": round(row[5] - row[6], 2),
            }
            for row in by_product
        ],
    }


@router.get("/purchase", summary="采购报表：按供应商 / 商品统计")
def purchase_report(
    start: str = Query(""),
    end: str = Query(""),
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("report:view")),
):
    start_dt, end_dt, start_s, end_s = _range(start, end)

    by_supplier = db.execute(
        select(
            Supplier.id,
            Supplier.name,
            func.count(PurchaseOrder.id),
            func.coalesce(func.sum(PurchaseOrder.total_amount), 0),
        )
        .join(PurchaseOrder, PurchaseOrder.supplier_id == Supplier.id)
        .where(
            PurchaseOrder.order_date >= start_dt,
            PurchaseOrder.order_date < end_dt,
            PurchaseOrder.status != "cancelled",
        )
        .group_by(Supplier.id, Supplier.name)
        .order_by(func.sum(PurchaseOrder.total_amount).desc())
    ).all()

    by_product = db.execute(
        select(
            Product.id,
            Product.name,
            Product.sku,
            Product.unit,
            func.coalesce(func.sum(PurchaseOrderItem.quantity), 0),
            func.coalesce(func.sum(PurchaseOrderItem.amount), 0),
        )
        .join(PurchaseOrderItem, PurchaseOrderItem.product_id == Product.id)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.order_id)
        .where(
            PurchaseOrder.order_date >= start_dt,
            PurchaseOrder.order_date < end_dt,
            PurchaseOrder.status != "cancelled",
        )
        .group_by(Product.id, Product.name, Product.sku, Product.unit)
        .order_by(func.sum(PurchaseOrderItem.amount).desc())
    ).all()

    return {
        "range": {"start": start_s, "end": end_s},
        "by_supplier": [
            {"supplier_id": row[0], "supplier_name": row[1], "order_count": row[2], "amount": round(row[3], 2)}
            for row in by_supplier
        ],
        "by_product": [
            {
                "product_id": row[0],
                "product_name": row[1],
                "sku": row[2],
                "unit": row[3],
                "quantity": int(row[4]),
                "amount": round(row[5], 2),
            }
            for row in by_product
        ],
    }


@router.get("/production", summary="生产报表：订单成本与完工统计")
def production_report(
    start: str = Query(""),
    end: str = Query(""),
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("report:view")),
):
    start_dt, end_dt, start_s, end_s = _range(start, end)
    orders = db.scalars(
        select(ProductionOrder)
        .where(ProductionOrder.created_at >= start_dt, ProductionOrder.created_at < end_dt)
        .order_by(ProductionOrder.id.desc())
    ).all()
    rows = [serialize_order(order) for order in orders]
    return {
        "range": {"start": start_s, "end": end_s},
        "totals": {
            "order_count": len(rows),
            "plan_qty": sum(row["quantity"] for row in rows),
            "completed_count": len([row for row in rows if row["status"] == "completed"]),
            "material_cost": round(sum(row["material_cost"] for row in rows), 2),
            "expense_cost": round(sum(row["expense_cost"] for row in rows), 2),
            "total_cost": round(sum(row["total_cost"] for row in rows), 2),
        },
        "rows": [
            {
                "id": row["id"],
                "order_no": row["order_no"],
                "product_name": row["product_name"],
                "quantity": row["quantity"],
                "status": row["status"],
                "material_cost": row["material_cost"],
                "expense_cost": row["expense_cost"],
                "total_cost": row["total_cost"],
                "process_done": row["process_done"],
                "process_total": row["process_total"],
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
            }
            for row in rows
        ],
    }
