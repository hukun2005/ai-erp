from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import require_perm
from ..models import Customer, Product, Quotation, QuotationItem, SalesOrder, SalesOrderItem, User
from ..schemas import QuotationCreate, QuotationStatus, QuotationUpdate
from ..scoping import apply_owner_scope
from ..utils import gen_order_no

router = APIRouter(prefix="/api/quotations", tags=["销售报价"])


def serialize_quote(quote: Quotation) -> dict:
    return {
        "id": quote.id,
        "quote_no": quote.quote_no,
        "customer_id": quote.customer_id,
        "customer_name": quote.customer.name if quote.customer else "",
        "status": quote.status,
        "valid_until": quote.valid_until,
        "total_amount": quote.total_amount,
        "discount_amount": quote.discount_amount,
        "converted_order_id": quote.converted_order_id,
        "remark": quote.remark,
        "created_by": quote.created_by,
        "created_at": quote.created_at,
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product.name if item.product else "",
                "product_sku": item.product.sku if item.product else "",
                "unit": item.product.unit if item.product else "",
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "amount": item.amount,
            }
            for item in quote.items
        ],
    }


def _get(db: Session, quote_id: int) -> Quotation:
    quote = db.get(Quotation, quote_id)
    if not quote:
        raise HTTPException(status_code=404, detail="报价单不存在")
    return quote


def _apply_items(db: Session, quote: Quotation, items) -> None:
    quote.items.clear()
    total = 0.0
    for entry in items:
        product_id = entry.get("product_id") if isinstance(entry, dict) else entry.product_id
        quantity = entry.get("quantity") if isinstance(entry, dict) else entry.quantity
        unit_price = entry.get("unit_price") if isinstance(entry, dict) else entry.unit_price
        product = db.get(Product, product_id)
        if not product:
            raise HTTPException(status_code=400, detail=f"商品 {product_id} 不存在")
        amount = round(quantity * unit_price, 2)
        total += amount
        quote.items.append(
            QuotationItem(
                product_id=product_id,
                quantity=quantity,
                unit_price=unit_price,
                amount=amount,
            )
        )
    quote.total_amount = round(total - (quote.discount_amount or 0), 2)


@router.get("", summary="报价单列表")
def list_quotes(
    keyword: str = Query(""),
    status: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("quote:view")),
):
    stmt = select(Quotation).join(Customer, Customer.id == Quotation.customer_id)
    stmt = apply_owner_scope(stmt, Quotation.created_by, user)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(Quotation.quote_no.like(like), Customer.name.like(like)))
    if status:
        stmt = stmt.where(Quotation.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Quotation.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"total": total, "items": [serialize_quote(row) for row in rows]}


@router.get("/{quote_id}", summary="报价单详情")
def get_quote(
    quote_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("quote:view")),
):
    return serialize_quote(_get(db, quote_id))


@router.post("", summary="新建报价单", dependencies=[Depends(require_perm("quote:manage"))])
def create_quote(
    payload: QuotationCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("quote:manage")),
):
    customer = db.get(Customer, payload.customer_id)
    if not customer:
        raise HTTPException(status_code=400, detail="客户不存在")
    quote = Quotation(
        quote_no=gen_order_no(db, Quotation, "QT", field="quote_no"),
        customer_id=payload.customer_id,
        valid_until=payload.valid_until,
        discount_amount=max(payload.discount_amount, 0),
        remark=payload.remark,
        created_by=user.username,
        status="draft",
    )
    _apply_items(db, quote, payload.items)
    db.add(quote)
    db.commit()
    db.refresh(quote)
    record(
        username=user.username,
        role=user.role,
        action="新建报价单",
        resource=f"报价单 {quote.quote_no}",
        detail=f"客户 {customer.name}，金额 {quote.total_amount:,.2f}",
        request=request,
    )
    return serialize_quote(quote)


@router.put("/{quote_id}", summary="编辑草稿报价单", dependencies=[Depends(require_perm("quote:manage"))])
def update_quote(
    quote_id: int,
    payload: QuotationUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("quote:manage")),
):
    quote = _get(db, quote_id)
    if quote.status != "draft":
        raise HTTPException(status_code=400, detail="只有草稿报价单可以编辑")
    data = payload.model_dump(exclude_unset=True)
    if data.get("customer_id"):
        customer = db.get(Customer, data["customer_id"])
        if not customer:
            raise HTTPException(status_code=400, detail="客户不存在")
        quote.customer_id = customer.id
    if "valid_until" in data:
        quote.valid_until = data["valid_until"]
    if "remark" in data:
        quote.remark = data["remark"] or ""
    if "discount_amount" in data:
        quote.discount_amount = max(data["discount_amount"] or 0, 0)
    if "items" in data:
        if not data["items"]:
            raise HTTPException(status_code=400, detail="报价明细不能为空")
        _apply_items(db, quote, data["items"])
    else:
        quote.total_amount = round(
            sum(item.amount for item in quote.items) - quote.discount_amount, 2
        )
    db.commit()
    db.refresh(quote)
    record(
        username=user.username,
        role=user.role,
        action="编辑报价单",
        resource=f"报价单 {quote.quote_no}",
        detail=f"金额 {quote.total_amount:,.2f}",
        request=request,
    )
    return serialize_quote(quote)


@router.post("/{quote_id}/status", summary="变更报价状态")
def change_status(
    quote_id: int,
    payload: QuotationStatus,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("quote:manage")),
):
    quote = _get(db, quote_id)
    if quote.status == "converted":
        raise HTTPException(status_code=400, detail="报价单已转订单，不能再变更状态")
    quote.status = payload.status
    db.commit()
    db.refresh(quote)
    return serialize_quote(quote)


@router.post("/{quote_id}/convert", summary="报价转销售订单（草稿）")
def convert_to_order(
    quote_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("quote:manage")),
):
    quote = _get(db, quote_id)
    if quote.status == "converted":
        raise HTTPException(status_code=400, detail="该报价单已转成销售订单")
    if quote.status == "rejected":
        raise HTTPException(status_code=400, detail="已失效的报价单不能转订单")
    if not quote.items:
        raise HTTPException(status_code=400, detail="报价单没有明细")

    order = SalesOrder(
        order_no=gen_order_no(db, SalesOrder, "SO"),
        customer_id=quote.customer_id,
        order_date=datetime.now(),
        discount_amount=quote.discount_amount,
        remark=f"由报价单 {quote.quote_no} 转入",
        created_by=user.username,
        status="draft",
    )
    total = 0.0
    for item in quote.items:
        amount = round(item.quantity * item.unit_price, 2)
        total += amount
        order.items.append(
            SalesOrderItem(
                product_id=item.product_id,
                quantity=item.quantity,
                unit_price=item.unit_price,
                amount=amount,
            )
        )
    order.total_amount = round(total - quote.discount_amount, 2)
    db.add(order)
    db.flush()
    quote.status = "converted"
    quote.converted_order_id = order.id
    db.commit()
    db.refresh(quote)
    record(
        username=user.username,
        role=user.role,
        action="报价转销售订单",
        resource=f"报价单 {quote.quote_no}",
        detail=f"生成销售订单 {order.order_no}，金额 {order.total_amount:,.2f}",
        request=request,
    )
    return {"quote": serialize_quote(quote), "order_no": order.order_no, "order_id": order.id}


@router.delete("/{quote_id}", summary="删除草稿报价单")
def delete_quote(
    quote_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("quote:manage")),
):
    quote = _get(db, quote_id)
    if quote.status != "draft":
        raise HTTPException(status_code=400, detail="只有草稿报价单可以删除")
    db.delete(quote)
    db.commit()
    return {"ok": True}
