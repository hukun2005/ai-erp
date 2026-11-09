from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import require_perm
from ..models import (
    Product,
    PurchaseInquiry,
    PurchaseInquiryQuote,
    PurchaseOrder,
    PurchaseOrderItem,
    Supplier,
    User,
)
from ..schemas import InquiryConvert, InquiryCreate, InquiryQuoteIn
from ..utils import gen_order_no

router = APIRouter(prefix="/api/purchase-inquiries", tags=["采购询价"])


def serialize_inquiry(doc: PurchaseInquiry) -> dict:
    return {
        "id": doc.id,
        "inquiry_no": doc.inquiry_no,
        "product_id": doc.product_id,
        "product_name": doc.product.name if doc.product else "",
        "product_sku": doc.product.sku if doc.product else "",
        "unit": doc.product.unit if doc.product else "",
        "quantity": doc.quantity,
        "status": doc.status,
        "deadline": doc.deadline,
        "remark": doc.remark,
        "converted_order_id": doc.converted_order_id,
        "created_by": doc.created_by,
        "created_at": doc.created_at,
        "quotes": [
            {
                "id": quote.id,
                "supplier_id": quote.supplier_id,
                "supplier_name": quote.supplier.name if quote.supplier else "",
                "unit_price": quote.unit_price,
                "amount": round(quote.unit_price * doc.quantity, 2),
                "remark": quote.remark,
            }
            for quote in sorted(doc.quotes, key=lambda q: q.unit_price)
        ],
    }


def _get(db: Session, doc_id: int) -> PurchaseInquiry:
    doc = db.get(PurchaseInquiry, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="询价单不存在")
    return doc


@router.get("", summary="询价单列表")
def list_inquiries(
    keyword: str = Query(""),
    status: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("purchase:view")),
):
    stmt = select(PurchaseInquiry).join(Product, Product.id == PurchaseInquiry.product_id)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(PurchaseInquiry.inquiry_no.like(like), Product.name.like(like)))
    if status:
        stmt = stmt.where(PurchaseInquiry.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(PurchaseInquiry.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"total": total, "items": [serialize_inquiry(row) for row in rows]}


@router.get("/{doc_id}", summary="询价单详情")
def get_inquiry(
    doc_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("purchase:view")),
):
    return serialize_inquiry(_get(db, doc_id))


@router.post("", summary="新建询价单", dependencies=[Depends(require_perm("purchase:manage"))])
def create_inquiry(
    payload: InquiryCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("purchase:manage")),
):
    product = db.get(Product, payload.product_id)
    if not product:
        raise HTTPException(status_code=400, detail="商品不存在")
    doc = PurchaseInquiry(
        inquiry_no=gen_order_no(db, PurchaseInquiry, "RFQ", field="inquiry_no"),
        product_id=payload.product_id,
        quantity=payload.quantity,
        deadline=payload.deadline,
        remark=payload.remark,
        created_by=user.username,
        status="draft",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    record(
        username=user.username,
        role=user.role,
        action="新建询价单",
        resource=f"询价单 {doc.inquiry_no}",
        detail=f"{product.name} × {doc.quantity}",
        request=request,
    )
    return serialize_inquiry(doc)


@router.post("/{doc_id}/quotes", summary="登记供应商报价")
def add_quote(
    doc_id: int,
    payload: InquiryQuoteIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("purchase:manage")),
):
    doc = _get(db, doc_id)
    if doc.status == "converted":
        raise HTTPException(status_code=400, detail="询价单已转采购订单，不能再录入报价")
    supplier = db.get(Supplier, payload.supplier_id)
    if not supplier:
        raise HTTPException(status_code=400, detail="供应商不存在")
    exists = next((q for q in doc.quotes if q.supplier_id == payload.supplier_id), None)
    if exists:
        raise HTTPException(status_code=400, detail=f"{supplier.name} 已有报价，不能重复录入")
    doc.quotes.append(
        PurchaseInquiryQuote(
            supplier_id=payload.supplier_id,
            unit_price=payload.unit_price,
            remark=payload.remark,
        )
    )
    doc.status = "open"
    db.commit()
    db.refresh(doc)
    return serialize_inquiry(doc)


@router.delete("/{doc_id}/quotes/{quote_id}", summary="删除供应商报价")
def delete_quote(
    doc_id: int,
    quote_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("purchase:manage")),
):
    doc = _get(db, doc_id)
    if doc.status == "converted":
        raise HTTPException(status_code=400, detail="询价单已转采购订单，不能删除报价")
    quote = db.get(PurchaseInquiryQuote, quote_id)
    if not quote or quote.inquiry_id != doc.id:
        raise HTTPException(status_code=404, detail="报价不存在")
    doc.quotes.remove(quote)
    if not doc.quotes:
        doc.status = "draft"
    db.commit()
    db.refresh(doc)
    return serialize_inquiry(doc)


@router.post("/{doc_id}/convert", summary="选定报价转采购订单（草稿）")
def convert_inquiry(
    doc_id: int,
    payload: InquiryConvert,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("purchase:manage")),
):
    doc = _get(db, doc_id)
    if doc.status == "converted":
        raise HTTPException(status_code=400, detail="该询价单已转采购订单")
    quote = db.get(PurchaseInquiryQuote, payload.quote_id)
    if not quote or quote.inquiry_id != doc.id:
        raise HTTPException(status_code=400, detail="请选择本询价单中的供应商报价")

    amount = round(quote.unit_price * doc.quantity, 2)
    order = PurchaseOrder(
        order_no=gen_order_no(db, PurchaseOrder, "PO"),
        supplier_id=quote.supplier_id,
        order_date=datetime.now(),
        total_amount=amount,
        remark=f"由询价单 {doc.inquiry_no} 转入，单价 {quote.unit_price:,.2f}",
        created_by=user.username,
        status="draft",
    )
    order.items.append(
        PurchaseOrderItem(
            product_id=doc.product_id,
            quantity=doc.quantity,
            unit_price=quote.unit_price,
            amount=amount,
        )
    )
    db.add(order)
    db.flush()
    doc.status = "converted"
    doc.converted_order_id = order.id
    db.commit()
    db.refresh(doc)
    record(
        username=user.username,
        role=user.role,
        action="询价转采购订单",
        resource=f"询价单 {doc.inquiry_no}",
        detail=f"生成采购订单 {order.order_no}，金额 {amount:,.2f}",
        request=request,
    )
    return {"inquiry": serialize_inquiry(doc), "order_no": order.order_no, "order_id": order.id}


@router.delete("/{doc_id}", summary="删除草稿询价单")
def delete_inquiry(
    doc_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("purchase:manage")),
):
    doc = _get(db, doc_id)
    if doc.status == "converted":
        raise HTTPException(status_code=400, detail="已转采购订单的询价单不能删除")
    db.delete(doc)
    db.commit()
    return {"ok": True}
