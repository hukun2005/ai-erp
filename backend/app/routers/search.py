"""全局搜索：一个输入框同时搜商品、客户、供应商与各类单据。

只返回当前角色有权查看的类别，并按数据范围过滤，
搜索结果里的 path/query 可以直接被前端拿去跳转，不需要前端再猜路由。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Customer, InboundOrder, Product, PurchaseOrder, SalesOrder, Supplier, User
from ..permissions import has_permission
from ..scoping import own_scope

router = APIRouter(prefix="/api/search", tags=["全局搜索"])

LIMIT = 5

SALES_STATUS = {
    "draft": "草稿",
    "pending": "待审批",
    "confirmed": "已确认",
    "completed": "已完成",
    "cancelled": "已取消",
}
PURCHASE_STATUS = {
    "draft": "草稿",
    "pending": "待审批",
    "ordered": "已下单",
    "received": "已入库",
    "cancelled": "已取消",
}


def _money(value: float) -> str:
    return f"¥{float(value or 0):,.2f}"


@router.get("", summary="全局搜索（跨模块）")
def global_search(
    q: str = Query("", max_length=50),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    keyword = (q or "").strip()
    if not keyword:
        return {"keyword": "", "groups": []}
    like = f"%{keyword}%"
    role = user.role
    owner = user.username if own_scope(role) else None
    groups: list[dict] = []

    def add(key: str, title: str, icon: str, items: list[dict]) -> None:
        if items:
            groups.append({"key": key, "title": title, "icon": icon, "items": items})

    if has_permission(role, "product:view"):
        rows = db.scalars(
            select(Product)
            .where(or_(Product.sku.like(like), Product.name.like(like), Product.spec.like(like)))
            .order_by(Product.id.desc())
            .limit(LIMIT)
        ).all()
        add(
            "products",
            "商品",
            "Goods",
            [
                {
                    "id": row.id,
                    "title": row.name,
                    "subtitle": f"{row.sku} · 库存 {row.stock_qty}{row.unit} · {_money(row.sale_price)}",
                    "path": "/products",
                    "query": {"keyword": row.sku},
                }
                for row in rows
            ],
        )

    if has_permission(role, "customer:view"):
        stmt = select(Customer).where(
            or_(
                Customer.name.like(like),
                Customer.contact.like(like),
                Customer.phone.like(like),
            )
        )
        if owner:
            stmt = stmt.where(or_(Customer.owner == owner, Customer.owner == ""))
        rows = db.scalars(stmt.order_by(Customer.id.desc()).limit(LIMIT)).all()
        add(
            "customers",
            "客户",
            "User",
            [
                {
                    "id": row.id,
                    "title": row.name,
                    "subtitle": " · ".join(
                        part for part in (row.contact, row.phone, f"{row.level} 级") if part
                    ),
                    "path": "/customers",
                    "query": {"keyword": row.name},
                }
                for row in rows
            ],
        )

    if has_permission(role, "supplier:view"):
        rows = db.scalars(
            select(Supplier)
            .where(or_(Supplier.name.like(like), Supplier.contact.like(like), Supplier.phone.like(like)))
            .order_by(Supplier.id.desc())
            .limit(LIMIT)
        ).all()
        add(
            "suppliers",
            "供应商",
            "Van",
            [
                {
                    "id": row.id,
                    "title": row.name,
                    "subtitle": " · ".join(part for part in (row.contact, row.phone, row.supply_category) if part),
                    "path": "/suppliers",
                    "query": {"keyword": row.name},
                }
                for row in rows
            ],
        )

    if has_permission(role, "sales:view"):
        stmt = select(SalesOrder).where(
            or_(SalesOrder.order_no.like(like), SalesOrder.remark.like(like))
        )
        if owner:
            stmt = stmt.where(SalesOrder.created_by == owner)
        order_rows = db.scalars(stmt.order_by(SalesOrder.id.desc()).limit(LIMIT)).all()
        add(
            "sales",
            "销售订单",
            "Sell",
            [
                {
                    "id": row.id,
                    "title": f"{row.order_no} · {row.customer.name if row.customer else '未知客户'}",
                    "subtitle": f"{SALES_STATUS.get(row.status, row.status)} · {_money(row.total_amount)}",
                    "path": "/sales-orders",
                    "query": {"keyword": row.order_no},
                }
                for row in order_rows
            ],
        )

    if has_permission(role, "purchase:view"):
        order_rows = db.scalars(
            select(PurchaseOrder)
            .where(or_(PurchaseOrder.order_no.like(like), PurchaseOrder.remark.like(like)))
            .order_by(PurchaseOrder.id.desc())
            .limit(LIMIT)
        ).all()
        add(
            "purchase",
            "采购订单",
            "ShoppingCart",
            [
                {
                    "id": row.id,
                    "title": f"{row.order_no} · {row.supplier.name if row.supplier else '未知供应商'}",
                    "subtitle": f"{PURCHASE_STATUS.get(row.status, row.status)} · {_money(row.total_amount)}",
                    "path": "/purchase-orders",
                    "query": {"keyword": row.order_no},
                }
                for row in order_rows
            ],
        )

    if has_permission(role, "inbound:view"):
        rows = db.scalars(
            select(InboundOrder)
            .where(or_(InboundOrder.inbound_no.like(like), InboundOrder.remark.like(like)))
            .order_by(InboundOrder.id.desc())
            .limit(LIMIT)
        ).all()
        add(
            "inbounds",
            "入库单",
            "Download",
            [
                {
                    "id": row.id,
                    "title": f"{row.inbound_no} · {row.supplier.name if row.supplier else '未知供应商'}",
                    "subtitle": f"{'已入库' if row.status == 'confirmed' else '草稿'} · {_money(row.total_amount)}",
                    "path": "/inbounds",
                    "query": {"keyword": row.inbound_no},
                }
                for row in rows
            ],
        )

    return {"keyword": keyword, "groups": groups}
