"""智能补货测算：AI 服务与 AI 工具共用的唯一口径。

补货量 = 日均销量 ×（采购周期 + 10 天）+ 1.2 × 安全库存 − 现有库存 − 在途采购量。
在途必须扣减，否则同一次缺货会被重复下单。

这个模块独立于 ai_service，避免「工具层 → ai_service → 工具层」的循环导入，
同时保证规则引擎、`/api/ai/replenishment` 接口和 AI 工具拿到的是同一份结果。
"""
import math

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Product, PurchaseOrder, PurchaseOrderItem, Supplier
from . import analytics


def get_replenishment(db: Session, lead_time: int = 15) -> list[dict]:
    """按近 30 天日均销量测算每个商品的建议补货量与建议供应商。

    lead_time 为采购周期（天）：可支撑天数低于该周期、或库存跌破安全线时才建议补货。
    """
    products = analytics.product_sales(db, 30)
    sold_map = {p["id"]: p["qty"] for p in products}
    all_products = db.scalars(select(Product).where(Product.is_active == True)).all()  # noqa: E712

    last_supplier = {}
    rows = db.execute(
        select(PurchaseOrderItem.product_id, PurchaseOrder.supplier_id)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.order_id)
        .order_by(PurchaseOrder.id.desc())
    ).all()
    for pid, sid in rows:
        last_supplier.setdefault(pid, sid)

    supplier_names = {s.id: s.name for s in db.scalars(select(Supplier)).all()}
    supplier_by_category = {s.supply_category: s.id for s in db.scalars(select(Supplier)).all()}

    # 在途采购量：已下单未入库的数量必须先扣掉，否则同一次缺货会被重复下单
    transit_rows = db.execute(
        select(PurchaseOrderItem.product_id, func.sum(PurchaseOrderItem.quantity))
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.order_id)
        .where(PurchaseOrder.status == "ordered")
        .group_by(PurchaseOrderItem.product_id)
    ).all()
    in_transit = {int(pid): int(qty or 0) for pid, qty in transit_rows}

    suggestions = []
    for p in all_products:
        avg_daily = sold_map.get(p.id, 0) / 30
        transit = in_transit.get(p.id, 0)
        available = p.stock_qty + transit
        cover_days = int(available / avg_daily) if avg_daily > 0 else None
        need = avg_daily * (lead_time + 10) + p.safety_stock * 1.2 - available
        if available > p.safety_stock and (cover_days is None or cover_days > lead_time):
            continue
        if need <= 0:  # 在途到货后已够用，本次不补
            continue
        suggested_qty = max(math.ceil(need), 1)
        supplier_id = last_supplier.get(p.id) or supplier_by_category.get(p.category)
        suggestions.append(
            {
                "product_id": p.id,
                "sku": p.sku,
                "name": p.name,
                "unit": p.unit,
                "stock_qty": p.stock_qty,
                "in_transit": transit,
                "safety_stock": p.safety_stock,
                "avg_daily_sales": round(avg_daily, 2),
                "days_of_cover": cover_days,
                "suggested_qty": suggested_qty,
                "estimated_cost": round(suggested_qty * p.cost_price, 2),
                "supplier_id": supplier_id,
                "supplier_name": supplier_names.get(supplier_id, "未关联供应商"),
                "priority": "高" if (cover_days is not None and cover_days <= 5) else "中",
                "reason": (
                    f"库存 {p.stock_qty}{p.unit} 低于安全库存 {p.safety_stock}{p.unit}"
                    if p.stock_qty <= p.safety_stock
                    else f"可支撑 {cover_days} 天，低于补货周期 {lead_time} 天"
                ),
            }
        )
    suggestions.sort(key=lambda x: (x["priority"] != "高", x["days_of_cover"] or 0))
    return suggestions
