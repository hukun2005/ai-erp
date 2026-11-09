"""只读查询工具：把「打开菜单 → 筛选 → 导出 → 手算」压缩成一次调用。

每个工具都必须遵守两条：
1. 数据范围在 handler 内用 apply_owner_scope / analytics 的 owner 参数施加，
   不信任模型传参；
2. 只返回回答所需字段：成本价、毛利类字段按权限裁剪，避免通过 AI 绕开字段级限制。
"""
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, or_, select

from ...models import (
    Customer,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseReturn,
    SalesOrder,
    SalesOrderItem,
    SalesReturn,
    StocktakeOrder,
    Supplier,
    Warehouse,
)
from ...permissions import has_permission
from ...scoping import apply_owner_scope
from ...services import analytics, approvals as approval_service, payables as payable_service
from .base import Tool, ToolContext
from .registry import register

# metric 与分组维度的合法组合：营收/订单数在订单粒度聚合，销量在明细粒度聚合
_SALES_DIMENSIONS = {
    "revenue": {"none", "day", "customer"},
    "orders": {"none", "day", "customer"},
    "qty": {"none", "day", "product", "category"},
}

_METRIC_UNITS = {"revenue": "元", "orders": "单", "qty": "件"}


# ---------------- 销售聚合 ----------------
class QuerySalesParams(BaseModel):
    metric: Literal["revenue", "orders", "qty"] = Field("revenue", description="汇总指标：营收/订单数/销量")
    group_by: Literal["none", "day", "customer", "product", "category"] = Field(
        "none", description="分组维度：不分组/按天/按客户/按商品/按品类"
    )
    days: int = Field(30, ge=1, le=365, description="统计最近多少天")

    @model_validator(mode="after")
    def _check_combination(self) -> "QuerySalesParams":
        """拒绝不支持的维度组合，避免返回口径错误的数字。"""
        if self.group_by not in _SALES_DIMENSIONS[self.metric]:
            allowed = "、".join(sorted(_SALES_DIMENSIONS[self.metric]))
            raise ValueError(f"metric={self.metric} 只支持 group_by 为 {allowed}")
        return self


def _order_amount_series(ctx: ToolContext, start: str, group_by: str) -> list[dict]:
    """在销售订单粒度聚合营收与订单数。"""
    stmt = select(
        func.sum(SalesOrder.total_amount).label("revenue"),
        func.count(SalesOrder.id).label("orders"),
    )
    stmt = apply_owner_scope(stmt, SalesOrder.created_by, ctx.user)
    stmt = stmt.where(SalesOrder.status.in_(analytics.VALID_SALES_STATUS))
    stmt = stmt.where(func.date(SalesOrder.order_date) >= start)

    if group_by == "none":
        row = ctx.db.execute(stmt).one()
        return [row]
    if group_by == "day":
        stmt = stmt.add_columns(func.date(SalesOrder.order_date).label("dim"))
        stmt = stmt.group_by(func.date(SalesOrder.order_date)).order_by(func.date(SalesOrder.order_date))
    else:  # customer
        stmt = stmt.add_columns(Customer.name.label("dim"))
        stmt = stmt.join(Customer, Customer.id == SalesOrder.customer_id)
        stmt = stmt.group_by(Customer.id)
        stmt = stmt.order_by(func.sum(SalesOrder.total_amount).desc())
    return ctx.db.execute(stmt).all()


def _item_qty_series(ctx: ToolContext, start: str, group_by: str) -> list[dict]:
    """在订单明细粒度聚合销量。"""
    stmt = select(func.sum(SalesOrderItem.quantity).label("qty"))
    stmt = stmt.join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
    stmt = apply_owner_scope(stmt, SalesOrder.created_by, ctx.user)
    stmt = stmt.where(SalesOrder.status.in_(analytics.VALID_SALES_STATUS))
    stmt = stmt.where(func.date(SalesOrder.order_date) >= start)

    if group_by == "none":
        return ctx.db.execute(stmt).all()
    if group_by == "day":
        stmt = stmt.add_columns(func.date(SalesOrder.order_date).label("dim"))
        stmt = stmt.group_by(func.date(SalesOrder.order_date)).order_by(func.date(SalesOrder.order_date))
        return ctx.db.execute(stmt).all()

    stmt = stmt.join(Product, Product.id == SalesOrderItem.product_id)
    column = Product.name if group_by == "product" else Product.category
    stmt = stmt.add_columns(column.label("dim"))
    stmt = stmt.group_by(column).order_by(func.sum(SalesOrderItem.quantity).desc())
    return ctx.db.execute(stmt).all()


def _query_sales(ctx: ToolContext, params: QuerySalesParams) -> dict:
    start = (datetime.now() - timedelta(days=params.days - 1)).date().isoformat()
    if params.metric == "qty":
        raw = _item_qty_series(ctx, start, params.group_by)
        rows = [
            {"name": str(r.dim) if params.group_by != "none" else "合计", "value": int(r.qty or 0)}
            for r in raw
        ]
    else:
        raw = _order_amount_series(ctx, start, params.group_by)
        key = "revenue" if params.metric == "revenue" else "orders"
        rows = [
            {
                "name": str(r.dim) if params.group_by != "none" else "合计",
                "value": round(float(r.revenue or 0), 2) if key == "revenue" else int(r.orders or 0),
            }
            for r in raw
        ]
    rows = [row for row in rows if row["name"] != "None"]
    return {
        "metric": params.metric,
        "group_by": params.group_by,
        "days": params.days,
        "unit": _METRIC_UNITS[params.metric],
        "total": round(sum(row["value"] for row in rows), 2),
        "rows": rows,
        "basis": "已确认 + 已完成订单",
    }


register(
    Tool(
        name="query_sales",
        title="销售数据聚合",
        description=(
            "按维度汇总销售数据，用于回答「近一个月卖了多少」「哪个品类最赚钱」"
            "「各客户贡献排名」这类问题。只统计已确认与已完成的订单，草稿不算。"
        ),
        params=QuerySalesParams,
        required_perm="sales:view",
        handler=_query_sales,
    )
)


# ---------------- 订单检索 ----------------
class SearchOrdersParams(BaseModel):
    keyword: str = Field("", max_length=50, description="单号或客户名关键字，留空表示不限")
    status: Literal["", "draft", "pending", "confirmed", "completed", "cancelled"] = Field(
        "", description="订单状态，留空表示全部"
    )
    days: int = Field(90, ge=1, le=730, description="只看最近多少天创建的订单")
    limit: int = Field(10, ge=1, le=50, description="最多返回多少条")


def _search_orders(ctx: ToolContext, params: SearchOrdersParams) -> dict:
    start = (datetime.now() - timedelta(days=params.days - 1)).date().isoformat()
    stmt = select(SalesOrder).join(Customer, Customer.id == SalesOrder.customer_id)
    stmt = apply_owner_scope(stmt, SalesOrder.created_by, ctx.user)
    stmt = stmt.where(func.date(SalesOrder.order_date) >= start)
    if params.keyword:
        like = f"%{params.keyword}%"
        stmt = stmt.where(or_(SalesOrder.order_no.like(like), Customer.name.like(like)))
    if params.status:
        stmt = stmt.where(SalesOrder.status == params.status)

    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    orders = ctx.db.scalars(stmt.order_by(SalesOrder.id.desc()).limit(params.limit)).all()
    return {
        "total": int(total),
        "rows": [
            {
                "order_no": order.order_no,
                "customer": order.customer.name if order.customer else "",
                "status": order.status,
                "order_date": order.order_date.strftime("%Y-%m-%d"),
                "total_amount": round(float(order.total_amount), 2),
                "created_by": order.created_by,
            }
            for order in orders
        ],
    }


register(
    Tool(
        name="search_orders",
        title="销售订单检索",
        description=(
            "按关键字、状态、时间范围检索销售订单，返回单号、客户、金额与状态。"
            "用于回答「张老板最近的单子」「有多少草稿单没确认」这类问题。"
        ),
        params=SearchOrdersParams,
        required_perm="sales:view",
        handler=_search_orders,
    )
)


# ---------------- 库存查询 ----------------
class GetStockParams(BaseModel):
    keyword: str = Field("", max_length=50, description="SKU 或商品名关键字，留空表示全部")
    only_shortage: bool = Field(False, description="只看低于安全库存的商品")
    limit: int = Field(10, ge=1, le=50, description="最多返回多少条")


def _in_transit(db) -> dict[int, int]:
    """已下单未入库的采购数量，按商品汇总。

    补货决策必须扣掉在途，否则同一次缺货会被重复下单。
    """
    rows = db.execute(
        select(PurchaseOrderItem.product_id, func.sum(PurchaseOrderItem.quantity))
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.order_id)
        .where(PurchaseOrder.status == "ordered")
        .group_by(PurchaseOrderItem.product_id)
    ).all()
    return {int(pid): int(qty or 0) for pid, qty in rows}


def _get_stock(ctx: ToolContext, params: GetStockParams) -> dict:
    stmt = select(Product).where(Product.is_active == True)  # noqa: E712
    if params.keyword:
        like = f"%{params.keyword}%"
        stmt = stmt.where(or_(Product.sku.like(like), Product.name.like(like)))
    if params.only_shortage:
        stmt = stmt.where(Product.stock_qty <= Product.safety_stock)
    stmt = stmt.order_by((Product.stock_qty - Product.safety_stock)).limit(params.limit)
    products = ctx.db.scalars(stmt).all()

    sold = {row["id"]: row["qty"] for row in analytics.product_sales(ctx.db, 30, ctx.owner)}
    transit = _in_transit(ctx.db)
    # 成本价只对能看到库存或财务的角色开放，销售员只能看数量
    show_cost = has_permission(ctx.user.role, "inventory:view") or has_permission(
        ctx.user.role, "finance:view"
    )

    rows = []
    for product in products:
        sold_qty = sold.get(product.id, 0)
        daily = sold_qty / 30 if sold_qty else 0
        row = {
            "sku": product.sku,
            "name": product.name,
            "unit": product.unit,
            "stock_qty": product.stock_qty,
            "safety_stock": product.safety_stock,
            "in_transit": transit.get(product.id, 0),
            "days_of_cover": int(product.stock_qty / daily) if daily > 0 else None,
            "status": (
                "缺货" if product.stock_qty <= 0 else ("预警" if product.stock_qty <= product.safety_stock else "正常")
            ),
        }
        if show_cost:
            row["cost_price"] = round(float(product.cost_price), 2)
            row["stock_value"] = round(product.stock_qty * float(product.cost_price), 2)
        rows.append(row)
    return {"rows": rows, "count": len(rows)}


register(
    Tool(
        name="get_stock",
        title="商品库存查询",
        description=(
            "查询商品现存量、安全库存、在途采购量和按近 30 天销量估算的可支撑天数。"
            "用于回答「A 商品还有多少」「哪些要断货了」。成本字段只对库存/财务角色返回。"
        ),
        params=GetStockParams,
        required_perm="product:view",
        handler=_get_stock,
    )
)


# ---------------- 成交价历史 ----------------
class PriceHistoryParams(BaseModel):
    product_id: int | None = Field(None, ge=1, description="商品 ID，与 keyword 二选一")
    keyword: str = Field("", max_length=50, description="商品名或 SKU 关键字，product_id 为空时使用")
    customer_id: int | None = Field(None, ge=1, description="只看某个客户的历史价")
    limit: int = Field(10, ge=1, le=50, description="最多返回多少条")


def _price_history(ctx: ToolContext, params: PriceHistoryParams) -> dict:
    stmt = (
        select(SalesOrderItem, SalesOrder, Customer.name, Product.name, Product.sku)
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
        .join(Customer, Customer.id == SalesOrder.customer_id)
        .join(Product, Product.id == SalesOrderItem.product_id)
    )
    stmt = apply_owner_scope(stmt, SalesOrder.created_by, ctx.user)
    stmt = stmt.where(SalesOrder.status.in_(analytics.VALID_SALES_STATUS))
    if params.product_id:
        stmt = stmt.where(SalesOrderItem.product_id == params.product_id)
    elif params.keyword:
        like = f"%{params.keyword}%"
        stmt = stmt.where(or_(Product.name.like(like), Product.sku.like(like)))
    if params.customer_id:
        stmt = stmt.where(SalesOrder.customer_id == params.customer_id)

    rows = ctx.db.execute(stmt.order_by(SalesOrder.id.desc()).limit(params.limit)).all()
    return {
        "rows": [
            {
                "order_no": order.order_no,
                "customer": customer_name,
                "product": product_name,
                "sku": sku,
                "order_date": order.order_date.strftime("%Y-%m-%d"),
                "quantity": item.quantity,
                "unit_price": round(float(item.unit_price), 2),
                "amount": round(float(item.amount), 2),
            }
            for item, order, customer_name, product_name, sku in rows
        ]
    }


register(
    Tool(
        name="get_price_history",
        title="历史成交价",
        description=(
            "查询某商品或某客户的历史成交单价，用于「按上次价格开单」「这个客户卖多少」。"
            "只统计已确认与已完成订单，草稿单与取消单不计入。"
        ),
        params=PriceHistoryParams,
        required_perm="sales:view",
        handler=_price_history,
    )
)


# ---------------- 应收估算 ----------------
class ReceivableParams(BaseModel):
    customer_id: int | None = Field(None, ge=1, description="只看某个客户")
    min_days: int = Field(0, ge=0, le=3650, description="只看账龄超过多少天的欠款")
    limit: int = Field(10, ge=1, le=100, description="最多返回多少个客户")


def _receivable(ctx: ToolContext, params: ReceivableParams) -> dict:
    stmt = (
        select(
            Customer.id,
            Customer.name,
            Customer.level,
            func.sum(SalesOrder.total_amount).label("amount"),
            func.count(SalesOrder.id).label("orders"),
            func.min(SalesOrder.order_date).label("oldest"),
        )
        .join(SalesOrder, SalesOrder.customer_id == Customer.id)
        .where(SalesOrder.status == "confirmed")
        .group_by(Customer.id)
        .order_by(func.sum(SalesOrder.total_amount).desc())
    )
    stmt = apply_owner_scope(stmt, SalesOrder.created_by, ctx.user)
    if params.customer_id:
        stmt = stmt.where(Customer.id == params.customer_id)

    today = datetime.now().date()
    items = []
    for row in ctx.db.execute(stmt).all():
        age_days = (today - row.oldest.date()).days if row.oldest else 0
        if age_days < params.min_days:
            continue
        items.append(
            {
                "customer_id": int(row.id),
                "customer": row.name,
                "level": row.level,
                "amount": round(float(row.amount or 0), 2),
                "orders": int(row.orders or 0),
                "age_days": age_days,
            }
        )
    items = items[: params.limit]
    return {
        "total": round(sum(item["amount"] for item in items), 2),
        "items": items,
        "note": "按「已确认未完成」订单估算，系统尚未记录收款核销，实际回款以财务对账为准",
    }


register(
    Tool(
        name="get_receivable",
        title="应收账龄估算",
        description=(
            "按客户汇总已确认未完成订单的应收金额与账龄，用于「谁还欠钱」「哪些客户逾期」。"
            "注意这是估算值：系统没有收款单，不能当作已核销的财务口径。"
        ),
        params=ReceivableParams,
        required_perm="finance:view",
        handler=_receivable,
    )
)


# ---------------- 客户画像 ----------------
class CustomerProfileParams(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=50, description="客户名称关键字")
    days: int = Field(90, ge=1, le=365, description="统计最近多少天的贡献")


def _customer_profile(ctx: ToolContext, params: CustomerProfileParams) -> dict:
    like = f"%{params.keyword}%"
    stmt = select(Customer).where(Customer.name.like(like))
    stmt = apply_owner_scope(stmt, Customer.owner, ctx.user)
    customer = ctx.db.scalars(stmt.order_by(Customer.id)).first()
    if customer is None:
        return {"found": False, "rows": [], "note": f"没有找到名称包含「{params.keyword}」的客户"}

    start = (datetime.now() - timedelta(days=params.days - 1)).date().isoformat()
    agg = ctx.db.execute(
        select(
            func.sum(SalesOrder.total_amount).label("amount"),
            func.count(SalesOrder.id).label("orders"),
            func.max(SalesOrder.order_date).label("last_order"),
        )
        .where(SalesOrder.customer_id == customer.id)
        .where(SalesOrder.status.in_(analytics.VALID_SALES_STATUS))
        .where(func.date(SalesOrder.order_date) >= start)
    ).one()

    recent = ctx.db.scalars(
        select(SalesOrder)
        .where(SalesOrder.customer_id == customer.id)
        .order_by(SalesOrder.id.desc())
        .limit(5)
    ).all()

    outstanding = ctx.db.scalar(
        select(func.coalesce(func.sum(SalesOrder.total_amount), 0))
        .where(SalesOrder.customer_id == customer.id)
        .where(SalesOrder.status == "confirmed")
    ) or 0

    last_order = agg.last_order
    return {
        "found": True,
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "level": customer.level,
            "owner": customer.owner or "公海",
            "contact": customer.contact,
            "phone": customer.phone,
            "credit_limit": round(float(customer.credit_limit), 2),
            "outstanding": round(float(outstanding), 2),
            "credit_available": round(float(customer.credit_limit) - float(outstanding), 2),
        },
        "period_days": params.days,
        "period_amount": round(float(agg.amount or 0), 2),
        "period_orders": int(agg.orders or 0),
        "last_order_date": last_order.strftime("%Y-%m-%d") if last_order else "",
        "days_since_last_order": (datetime.now().date() - last_order.date()).days if last_order else None,
        "recent_orders": [
            {
                "order_no": order.order_no,
                "status": order.status,
                "order_date": order.order_date.strftime("%Y-%m-%d"),
                "total_amount": round(float(order.total_amount), 2),
            }
            for order in recent
        ],
    }


register(
    Tool(
        name="get_customer_profile",
        title="客户画像",
        description=(
            "按名称查单个客户的等级、负责销售、信用额度与已用额度、近 N 天贡献、"
            "最近下单时间和最近 5 张订单。用于开单前评估和客户回访。"
        ),
        params=CustomerProfileParams,
        required_perm="customer:view",
        handler=_customer_profile,
    )
)


# ---------------- 订单审批 ----------------
class PendingApprovalsParams(BaseModel):
    order_type: Literal["", "sales", "purchase"] = Field("", description="单据类型，留空表示销售 + 采购")
    limit: int = Field(10, ge=1, le=50, description="最多返回多少条")


def _pending_approvals(ctx: ToolContext, params: PendingApprovalsParams) -> dict:
    """待审批列表：只返回当前角色有权审批的单据类型，避免越权看到别部门的单。"""
    allowed = {
        kind
        for kind, perm in (("sales", "sales:approve"), ("purchase", "purchase:approve"))
        if has_permission(ctx.user.role, perm)
    }
    items = [row for row in approval_service.pending_orders(ctx.db) if row["order_type"] in allowed]
    if params.order_type:
        items = [row for row in items if row["order_type"] == params.order_type]
    total_amount = round(sum(row["total_amount"] for row in items), 2)
    return {
        "count": len(items),
        "total_amount": total_amount,
        "rows": [
            {
                "order_type": row["order_type"],
                "order_no": row["order_no"],
                "partner_name": row["partner_name"],
                "total_amount": row["total_amount"],
                "created_by": row["created_by"],
                "order_date": row["order_date"].strftime("%Y-%m-%d"),
                "item_summary": row["item_summary"],
            }
            for row in items[: params.limit]
        ],
        "note": "审批通过销售订单会立即扣减库存；采购订单通过后状态变为已下单待入库",
    }


register(
    Tool(
        name="query_pending_approvals",
        title="待审批单据",
        description=(
            "查询当前待审批的销售与采购订单，返回单号、往来单位、金额、提交人和商品摘要。"
            "用于回答「有哪些单子等我批」「待审批金额多大」。只返回当前角色有审批权的单据类型。"
        ),
        params=PendingApprovalsParams,
        required_perm="sales:approve",
        handler=_pending_approvals,
    )
)


# ---------------- 退货记录 ----------------
_RETURN_STATUS_LABELS = {"draft": "草稿", "confirmed": "已确认", "cancelled": "已取消"}


class SearchReturnsParams(BaseModel):
    kind: Literal["sales", "purchase"] = Field("sales", description="sales=销售退货，purchase=采购退货")
    keyword: str = Field("", max_length=50, description="退货单号或客户/供应商名称关键字，留空表示不限")
    status: Literal["", "draft", "confirmed", "cancelled"] = Field("", description="退货单状态，留空表示全部")
    days: int = Field(90, ge=1, le=730, description="只看最近多少天创建的退货单")
    limit: int = Field(10, ge=1, le=50, description="最多返回多少条")


def _search_returns(ctx: ToolContext, params: SearchReturnsParams) -> dict:
    start = (datetime.now() - timedelta(days=params.days - 1)).date().isoformat()
    if params.kind == "sales":
        model, partner_model, partner_col = SalesReturn, Customer, SalesReturn.customer_id
    else:
        model, partner_model, partner_col = PurchaseReturn, Supplier, PurchaseReturn.supplier_id

    base = [func.date(model.created_at) >= start]
    if params.keyword:
        like = f"%{params.keyword}%"
        base.append(or_(model.return_no.like(like), partner_model.name.like(like)))

    def _scoped(stmt):
        return apply_owner_scope(stmt, model.created_by, ctx.user)

    stmt = _scoped(select(model).join(partner_model, partner_model.id == partner_col)).where(*base)
    if params.status:
        stmt = stmt.where(model.status == params.status)

    count_stmt = _scoped(select(func.count()).select_from(model).join(
        partner_model, partner_model.id == partner_col
    )).where(*base)
    if params.status:
        count_stmt = count_stmt.where(model.status == params.status)
    total_count = ctx.db.scalar(count_stmt) or 0

    confirmed_total = ctx.db.scalar(
        _scoped(select(func.coalesce(func.sum(model.total_amount), 0)).select_from(model).join(
            partner_model, partner_model.id == partner_col
        )).where(*base).where(model.status == "confirmed")
    ) or 0

    records = ctx.db.scalars(stmt.order_by(model.id.desc()).limit(params.limit)).all()
    rows = []
    for ret in records:
        partner = ret.customer if params.kind == "sales" else ret.supplier
        rows.append(
            {
                "return_no": ret.return_no,
                "order_no": ret.order.order_no if ret.order else "",
                "partner": partner.name if partner else "",
                "status": _RETURN_STATUS_LABELS.get(ret.status, ret.status),
                "total_amount": round(float(ret.total_amount or 0), 2),
                "reason": ret.reason or "",
                "created_at": ret.created_at.strftime("%Y-%m-%d"),
                "items": "、".join(
                    f"{item.product.name if item.product else item.product_id}×{item.quantity}"
                    for item in ret.items[:3]
                ),
            }
        )
    return {
        "kind": params.kind,
        "count": int(total_count),
        "confirmed_total": round(float(confirmed_total), 2),
        "rows": rows,
        "basis": "只有已确认的退货单会冲减应收/应付并回冲库存，草稿与已取消不计",
    }


register(
    Tool(
        name="search_returns",
        title="退货记录检索",
        description=(
            "检索销售或采购退货单，返回退货单号、原单号、往来单位、状态、金额与原因。"
            "用于回答「最近退了哪些货」「有多少退货还在草稿」。不影响财务口径，仅供查询。"
        ),
        params=SearchReturnsParams,
        required_perm="return:view",
        handler=_search_returns,
    )
)


# ---------------- 盘点 ----------------
class StocktakeQueryParams(BaseModel):
    status: Literal["", "draft", "completed", "cancelled"] = Field("", description="盘点单状态，留空表示全部")
    warehouse: str = Field("", max_length=50, description="仓库名称关键字，留空表示全部")
    days: int = Field(90, ge=1, le=730, description="只看最近多少天创建的盘点单")
    limit: int = Field(10, ge=1, le=50, description="最多返回多少张盘点单")


def _stocktakes(ctx: ToolContext, params: StocktakeQueryParams) -> dict:
    start = (datetime.now() - timedelta(days=params.days - 1)).date().isoformat()
    stmt = select(StocktakeOrder)
    stmt = apply_owner_scope(stmt, StocktakeOrder.created_by, ctx.user)
    stmt = stmt.where(func.date(StocktakeOrder.created_at) >= start)
    if params.status:
        stmt = stmt.where(StocktakeOrder.status == params.status)
    if params.warehouse:
        stmt = stmt.join(Warehouse, Warehouse.id == StocktakeOrder.warehouse_id).where(
            Warehouse.name.like(f"%{params.warehouse}%")
        )
    orders = ctx.db.scalars(stmt.order_by(StocktakeOrder.id.desc()).limit(params.limit)).all()

    rows = []
    for order in orders:
        diffs = [item for item in order.items if item.diff_qty]
        rows.append(
            {
                "stocktake_no": order.stocktake_no,
                "warehouse": order.warehouse.name if order.warehouse else "",
                "status": {"draft": "草稿", "completed": "已完成", "cancelled": "已取消"}.get(
                    order.status, order.status
                ),
                "created_at": order.created_at.strftime("%Y-%m-%d"),
                "item_count": len(order.items),
                "diff_count": len(diffs),
                "gain_qty": sum(item.diff_qty for item in diffs if item.diff_qty > 0),
                "loss_qty": sum(-item.diff_qty for item in diffs if item.diff_qty < 0),
                "diff_products": "、".join(
                    f"{item.product.name if item.product else item.product_id}"
                    f"（{'+' if item.diff_qty > 0 else ''}{item.diff_qty}）"
                    for item in diffs[:3]
                ),
            }
        )
    return {
        "count": len(rows),
        "rows": rows,
        "note": "盘盈盘亏只在盘点单完成时写入库存台账，草稿盘点不影响账面库存",
    }


register(
    Tool(
        name="query_stocktakes",
        title="盘点差异查询",
        description=(
            "查询最近的库存盘点单，返回仓库、状态、盘点行数、差异行数与盘盈盘亏数量。"
            "用于回答「最近盘点有没有差异」「哪些盘点还没完成」。"
        ),
        params=StocktakeQueryParams,
        required_perm="stocktake:view",
        handler=_stocktakes,
    )
)


# ---------------- 应付账款 ----------------
class PayablesParams(BaseModel):
    supplier: str = Field("", max_length=50, description="供应商名称关键字，留空表示全部")
    min_days: int = Field(0, ge=0, le=3650, description="只看账龄超过多少天的应付")
    limit: int = Field(10, ge=1, le=50, description="最多返回多少个供应商")


def _payables(ctx: ToolContext, params: PayablesParams) -> dict:
    items = payable_service.supplier_payables(ctx.db)
    if params.supplier:
        items = [item for item in items if params.supplier in item["supplier"]]
    if params.min_days:
        items = [item for item in items if item["age_days"] >= params.min_days]

    buckets: dict[str, float] = {}
    for item in items:
        buckets[item["bucket"]] = round(buckets.get(item["bucket"], 0.0) + item["balance"], 2)
    return {
        "total": round(sum(item["balance"] for item in items), 2),
        "count": len(items),
        "buckets": [{"bucket": key, "amount": value} for key, value in buckets.items()],
        "rows": items[: params.limit],
        "basis": "应付 = 已入库采购 − 已确认采购退货 − 已生效付款核销；付款单作废会自动回退",
    }


register(
    Tool(
        name="get_payables",
        title="应付账款与账龄",
        description=(
            "按供应商汇总应付余额与账龄分档（0-30/31-60/60 天以上），返回已付款、已退货与未结金额。"
            "用于回答「还欠供应商多少钱」「哪些供应商账龄最长」「这个月付了多少」。"
        ),
        params=PayablesParams,
        required_perm="payable:view",
        handler=_payables,
    )
)
