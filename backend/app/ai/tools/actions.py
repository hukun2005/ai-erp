"""开单类工具：只产出「预览 + 待确认草稿」，绝不写库。

设计原则：AI 负责解析自然语言和查历史价，落库这一步永远由用户点确认、
走已有的 POST /api/sales-orders 接口完成。这样即使模型被诱导，
也不可能凭空生成一张订单。
"""
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ...models import Customer, Product, SalesOrder, SalesOrderItem
from ...scoping import apply_owner_scope
from ...services import analytics
from .base import Tool, ToolContext
from .registry import register


class ProposalItem(BaseModel):
    product: str = Field(..., min_length=1, max_length=50, description="商品名称或 SKU，支持模糊匹配")
    quantity: int = Field(..., ge=1, le=100000, description="数量")
    unit_price: float | None = Field(
        None, ge=0, description="指定单价；留空则取该客户最近成交价，再退回商品售价"
    )


class ProposeSalesOrderParams(BaseModel):
    customer: str = Field(..., min_length=1, max_length=50, description="客户名称关键字")
    items: list[ProposalItem] = Field(..., min_length=1, max_length=50, description="订单明细")
    remark: str = Field("", max_length=200, description="备注，例如交期要求")


def _resolve_customer(db, keyword: str, user):
    """先精确匹配名称，再按关键字模糊匹配；都按数据范围过滤。"""
    for stmt in (
        select(Customer).where(Customer.name == keyword),
        select(Customer).where(Customer.name.like(f"%{keyword}%")),
    ):
        customer = db.scalars(apply_owner_scope(stmt, Customer.owner, user).order_by(Customer.id)).first()
        if customer:
            return customer
    return None


def _resolve_product(db, keyword: str) -> Product | None:
    """SKU 精确优先，再按名称模糊匹配。"""
    product = db.scalar(select(Product).where(Product.sku == keyword))
    if product:
        return product
    return db.scalars(
        select(Product).where(Product.name.like(f"%{keyword}%")).order_by(Product.id)
    ).first()


def _last_deal_price(db, customer_id: int, product_id: int, user) -> float | None:
    """该客户最近一次的成交单价，用于「按上次价格开单」。"""
    stmt = (
        select(SalesOrderItem.unit_price)
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
        .where(SalesOrder.customer_id == customer_id)
        .where(SalesOrderItem.product_id == product_id)
        .where(SalesOrder.status.in_(analytics.VALID_SALES_STATUS))
        .order_by(SalesOrder.id.desc())
        .limit(1)
    )
    value = db.scalar(apply_owner_scope(stmt, SalesOrder.created_by, user))
    return round(float(value), 2) if value is not None else None


def _propose_sales_order(ctx: ToolContext, params: ProposeSalesOrderParams) -> dict:
    customer = _resolve_customer(ctx.db, params.customer, ctx.user)
    if customer is None:
        return {
            "found": False,
            "items": [],
            "note": f"没有找到名称包含「{params.customer}」的客户，请确认客户名称或先新建客户",
        }

    outstanding = ctx.db.scalar(
        select(func.coalesce(func.sum(SalesOrder.total_amount), 0))
        .where(SalesOrder.customer_id == customer.id)
        .where(SalesOrder.status == "confirmed")
    ) or 0

    rows: list[dict] = []
    draft_items: list[dict] = []
    warnings: list[str] = []
    total = 0.0

    for raw in params.items:
        product = _resolve_product(ctx.db, raw.product)
        if product is None:
            warnings.append(f"没有找到商品「{raw.product}」，该行已跳过")
            continue
        if raw.unit_price is not None:
            price, source = round(float(raw.unit_price), 2), "指定价"
        else:
            last = _last_deal_price(ctx.db, customer.id, product.id, ctx.user)
            if last is not None:
                price, source = last, "最近成交价"
            else:
                price, source = round(float(product.sale_price), 2), "商品售价"

        amount = round(raw.quantity * price, 2)
        total += amount
        shortage = max(raw.quantity - product.stock_qty, 0)
        if shortage:
            warnings.append(f"{product.name} 库存不足：需 {raw.quantity}{product.unit}，现有 {product.stock_qty}")

        rows.append(
            {
                "name": product.name,
                "sku": product.sku,
                "quantity": raw.quantity,
                "unit": product.unit,
                "unit_price": price,
                "amount": amount,
                "price_source": source,
                "stock_qty": product.stock_qty,
                "shortage": shortage,
            }
        )
        draft_items.append(
            {"product_id": product.id, "quantity": raw.quantity, "unit_price": price}
        )

    if not rows:
        return {
            "found": False,
            "items": [],
            "note": "明细里的商品一个都没匹配上，请核对商品名称或 SKU",
        }

    credit_limit = round(float(customer.credit_limit), 2)
    if credit_limit > 0 and float(outstanding) + total > credit_limit:
        # 只提示不拦截：是否放行由业务自行决定，真正的拦截规则另行制定
        warnings.append(
            f"客户已用额度 {float(outstanding):,.2f}，本单 {total:,.2f}，"
            f"合计超过信用额度 {credit_limit:,.2f}（仅提示，不拦截）"
        )

    return {
        "found": True,
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "level": customer.level,
            "owner": customer.owner or "公海",
            "credit_limit": credit_limit,
            "outstanding": round(float(outstanding), 2),
            "credit_available": round(credit_limit - float(outstanding), 2),
        },
        "items": rows,
        "total_amount": round(total, 2),
        "warnings": warnings,
        "draft": {
            "customer_id": customer.id,
            "discount_amount": 0,
            "remark": params.remark or "由 AI 助手生成，待确认",
            "items": draft_items,
        },
        "note": "以上只是预览，尚未建单；需要用户在界面上点「确认建单」才会写入草稿订单",
    }


register(
    Tool(
        name="propose_sales_order",
        title="开单预览",
        description=(
            "把用户的自然语言开单请求转成待确认的订单草稿：解析商品、带出该客户最近成交价、"
            "校验库存与信用额度提示，返回可编辑的明细。本工具不会写入数据库，"
            "必须在回答中提示用户确认后才能建单。"
        ),
        params=ProposeSalesOrderParams,
        required_perm="sales:manage",
        handler=_propose_sales_order,
        read_only=True,
    )
)
