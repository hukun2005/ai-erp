"""主数据与采购单据的只读查询工具：商品、客户、采购单。

与 queries.py 的分工：这里补的是「开单前查价、回访前查客户、跟单查采购进度」最常问到的
三类对象。字段裁剪与数据范围仍由工具层统一施加，不信任模型传参。
"""
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from ...models import Customer, Product, PurchaseOrder, SalesOrder, Supplier
from ...permissions import has_permission
from ...scoping import apply_owner_scope
from ...services import analytics
from .base import Tool, ToolContext
from .registry import register

_PURCHASE_STATUS_LABELS = {
    "draft": "草稿",
    "pending": "待审批",
    "ordered": "已下单待入库",
    "received": "已入库",
    "cancelled": "已取消",
}


# ---------------- 商品检索 ----------------
class SearchProductsParams(BaseModel):
    keyword: str = Field("", max_length=50, description="商品名称/SKU/品类关键字，留空表示全部")
    only_low_stock: bool = Field(False, description="只看现存量低于安全库存的商品")
    limit: int = Field(10, ge=1, le=50, description="最多返回多少条")


def _search_products(ctx: ToolContext, params: SearchProductsParams) -> dict:
    stmt = select(Product).where(Product.is_active == True)  # noqa: E712
    if params.keyword:
        like = f"%{params.keyword}%"
        stmt = stmt.where(
            or_(Product.name.like(like), Product.sku.like(like), Product.category.like(like))
        )
    if params.only_low_stock:
        stmt = stmt.where(Product.stock_qty <= Product.safety_stock)
    stmt = stmt.order_by((Product.stock_qty - Product.safety_stock), Product.id).limit(params.limit)

    # 成本价只对库存/财务角色开放，销售员查商品只能看到售价与数量
    show_cost = has_permission(ctx.user.role, "inventory:view") or has_permission(
        ctx.user.role, "finance:view"
    )
    rows = []
    for product in ctx.db.scalars(stmt).all():
        row = {
            "sku": product.sku,
            "name": product.name,
            "category": product.category,
            "unit": product.unit,
            "sale_price": round(float(product.sale_price), 2),
            "stock_qty": product.stock_qty,
            "safety_stock": product.safety_stock,
            "status": (
                "缺货"
                if product.stock_qty <= 0
                else ("预警" if product.stock_qty <= product.safety_stock else "正常")
            ),
        }
        if show_cost:
            row["cost_price"] = round(float(product.cost_price), 2)
        rows.append(row)

    return {
        "count": len(rows),
        "rows": rows,
        "note": "售价是标准售价；回答客户实际成交价请改用 get_price_history 查历史成交价",
    }


register(
    Tool(
        name="search_products",
        title="商品检索",
        description=(
            "按名称/SKU/品类查商品，返回标准售价、现存量、安全库存和是否缺货。"
            "用于回答「XX 卖多少钱」「店里有哪些电动工具」「这个商品还有货吗」。"
            "成本价只对库存/财务角色返回。"
        ),
        params=SearchProductsParams,
        required_perm="product:view",
        handler=_search_products,
    )
)


# ---------------- 客户检索 ----------------
class SearchCustomersParams(BaseModel):
    keyword: str = Field("", max_length=50, description="客户名称/联系人/电话关键字，留空表示全部")
    limit: int = Field(10, ge=1, le=50, description="最多返回多少个客户")


def _search_customers(ctx: ToolContext, params: SearchCustomersParams) -> dict:
    stmt = select(Customer)
    if params.keyword:
        like = f"%{params.keyword}%"
        stmt = stmt.where(
            or_(Customer.name.like(like), Customer.contact.like(like), Customer.phone.like(like))
        )
    stmt = apply_owner_scope(stmt, Customer.owner, ctx.user)
    customers = ctx.db.scalars(stmt.order_by(Customer.id).limit(params.limit)).all()
    if not customers:
        return {"count": 0, "rows": [], "note": f"没有找到与「{params.keyword}」匹配的客户"}

    ids = [customer.id for customer in customers]
    outstanding_rows = ctx.db.execute(
        select(SalesOrder.customer_id, func.sum(SalesOrder.total_amount))
        .where(SalesOrder.customer_id.in_(ids))
        .where(SalesOrder.status == "confirmed")
        .group_by(SalesOrder.customer_id)
    ).all()
    outstanding = {int(cid): round(float(amount or 0), 2) for cid, amount in outstanding_rows}

    last_rows = ctx.db.execute(
        select(SalesOrder.customer_id, func.max(SalesOrder.order_date))
        .where(SalesOrder.customer_id.in_(ids))
        .group_by(SalesOrder.customer_id)
    ).all()
    last_order = {int(cid): last for cid, last in last_rows}

    today = datetime.now().date()
    rows = []
    for customer in customers:
        last = last_order.get(customer.id)
        credit_limit = round(float(customer.credit_limit), 2)
        used = outstanding.get(customer.id, 0.0)
        rows.append(
            {
                "customer_id": customer.id,
                "name": customer.name,
                "level": customer.level,
                "owner": customer.owner or "公海",
                "contact": customer.contact,
                "phone": customer.phone,
                "credit_limit": credit_limit,
                "outstanding": used,
                "credit_available": round(credit_limit - used, 2),
                "last_order_date": last.strftime("%Y-%m-%d") if last else "",
                "days_since_last_order": (today - last.date()).days if last else None,
            }
        )
    return {
        "count": len(rows),
        "rows": rows,
        "note": "应收为已确认未完成订单金额估算，不代表已对账核销；负责人为空表示公海客户",
    }


register(
    Tool(
        name="search_customers",
        title="客户检索",
        description=(
            "按名称/联系人/电话查客户，返回等级、负责人、信用额度与已用额度、最近下单时间。"
            "用于回答「张老板是哪个销售在跟」「哪些客户额度快用完了」。"
            "数据范围按角色过滤，销售员只能看到自己负责的客户。"
        ),
        params=SearchCustomersParams,
        required_perm="customer:view",
        handler=_search_customers,
    )
)


# ---------------- 客户流失风险 ----------------
class CustomerRetentionParams(BaseModel):
    days: int = Field(30, ge=7, le=365, description="统计窗口（天），用于计算贡献金额与订单数")
    min_days_since_last: int = Field(30, ge=0, le=365, description="只看超过多少天没下单的客户")
    limit: int = Field(10, ge=1, le=50, description="最多返回多少个客户")


def _customer_retention(ctx: ToolContext, params: CustomerRetentionParams) -> dict:
    stats = analytics.customer_stats(ctx.db, params.days, ctx.owner)
    risky = [
        row
        for row in stats
        if row["last_order"] and (row["days_since_last"] or 0) >= params.min_days_since_last
    ]
    risky.sort(key=lambda row: row["days_since_last"], reverse=True)
    rows = [
        {
            "name": row["name"],
            "level": row["level"],
            "last_order_date": row["last_order"],
            "days_since_last_order": row["days_since_last"],
            "period_amount": row["amount"],
            "period_orders": row["orders"],
        }
        for row in risky[: params.limit]
    ]
    return {
        "window_days": params.days,
        "min_days_since_last": params.min_days_since_last,
        "count": len(rows),
        "rows": rows,
        "note": "按最后下单时间倒序；金额是统计窗口内已确认+已完成订单的贡献，草稿与已取消不计",
    }


register(
    Tool(
        name="get_customer_retention",
        title="客户流失风险",
        description=(
            "按最后下单时间找出最久没下单的客户，返回未下单天数、窗口内贡献金额与订单数。"
            "用于回答「哪些客户可能流失」「哪些客户一个月没下单了」「该优先回访谁」。"
            "一次调用即可拿到全部候选客户，不需要逐个查客户画像。"
        ),
        params=CustomerRetentionParams,
        required_perm="customer:view",
        handler=_customer_retention,
    )
)


# ---------------- 采购单检索 ----------------
class SearchPurchaseOrdersParams(BaseModel):
    keyword: str = Field("", max_length=50, description="采购单号或供应商名称关键字，留空表示不限")
    status: Literal["", "draft", "pending", "ordered", "received", "cancelled"] = Field(
        "", description="采购单状态，留空表示全部"
    )
    days: int = Field(90, ge=1, le=730, description="只看最近多少天创建的采购单")
    limit: int = Field(10, ge=1, le=50, description="最多返回多少条")


def _search_purchase_orders(ctx: ToolContext, params: SearchPurchaseOrdersParams) -> dict:
    start = (datetime.now() - timedelta(days=params.days - 1)).date().isoformat()
    conditions = [func.date(PurchaseOrder.order_date) >= start]
    if params.status:
        conditions.append(PurchaseOrder.status == params.status)
    if params.keyword:
        like = f"%{params.keyword}%"
        conditions.append(or_(PurchaseOrder.order_no.like(like), Supplier.name.like(like)))

    base = (
        select(PurchaseOrder)
        .join(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .where(*conditions)
    )
    base = apply_owner_scope(base, PurchaseOrder.created_by, ctx.user)

    matched = base.subquery()
    total = ctx.db.scalar(select(func.count()).select_from(matched)) or 0
    amount = ctx.db.scalar(select(func.coalesce(func.sum(matched.c.total_amount), 0))) or 0
    orders = ctx.db.scalars(base.order_by(PurchaseOrder.id.desc()).limit(params.limit)).all()

    return {
        "count": int(total),
        "total_amount": round(float(amount), 2),
        "rows": [
            {
                "order_no": order.order_no,
                "supplier": order.supplier.name if order.supplier else "",
                "status": _PURCHASE_STATUS_LABELS.get(order.status, order.status),
                "order_date": order.order_date.strftime("%Y-%m-%d"),
                "total_amount": round(float(order.total_amount), 2),
                "item_count": len(order.items),
                "created_by": order.created_by,
            }
            for order in orders
        ],
        "note": "「已下单待入库」才是真正的在途采购；草稿与待审批不会占用库存",
    }


register(
    Tool(
        name="search_purchase_orders",
        title="采购单检索",
        description=(
            "按单号、供应商、状态和时间范围检索采购单，返回金额、状态与明细行数。"
            "用于回答「还有哪些采购没入库」「上个月向某供应商下了多少单」。"
        ),
        params=SearchPurchaseOrdersParams,
        required_perm="purchase:view",
        handler=_search_purchase_orders,
    )
)
