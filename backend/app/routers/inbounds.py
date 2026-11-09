from datetime import datetime
import json

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..audit import record
from ..database import get_db
from ..deps import require_perm
from ..models import InboundItem, InboundOrder, Product, PurchaseOrder, Supplier, User
from ..schemas import InboundAction, InboundCreate, InboundOut
from ..scoping import apply_owner_scope, ensure_owner
from ..services import matching
from ..services.docx_render import build_docx_download, build_inbound_document, money
from ..services.product_type import ensure_product_type
from ..services.stock import apply_stock_change
from ..utils import gen_order_no, photo_pages
from .vision import recognize_uploaded

router = APIRouter(prefix="/api/inbounds", tags=["入库单"])

STATUS_TEXT = {
    "draft": "草稿",
    "confirmed": "已入库",
    "cancelled": "已取消",
}


def _serialize(row: InboundOrder) -> dict:
    # 照片列表在库里是 JSON 字符串，先还原再交给 schema 校验
    values = {column.name: getattr(row, column.name) for column in InboundOrder.__table__.columns}
    values["photo_paths"] = photo_pages(row.photo_paths)
    payload = InboundOut.model_validate(values).model_dump()
    payload.update(
        supplier_name=row.supplier.name if row.supplier else "",
        warehouse_name=row.warehouse.name if row.warehouse else "",
        location_code=row.location.code if row.location else "",
        purchase_order_no=row.purchase_order.order_no if row.purchase_order else "",
        photo_paths=photo_pages(row.photo_paths),
        items=[
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product.name if item.product else "",
                "product_sku": item.product.sku if item.product else "",
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "amount": item.amount,
                "batch_no": item.batch_no,
                "expiry_date": item.expiry_date,
                "remark": item.remark,
            }
            for item in row.items
        ],
    )
    return payload


def _get(db: Session, inbound_id: int) -> InboundOrder:
    row = db.scalar(
        select(InboundOrder)
        .where(InboundOrder.id == inbound_id)
        .options(selectinload(InboundOrder.items))
    )
    if not row:
        raise HTTPException(status_code=404, detail="入库单不存在")
    return row


def _build_items(db: Session, items) -> tuple[list[InboundItem], float]:
    if not items:
        raise HTTPException(status_code=400, detail="请至少添加一条入库明细")
    rows: list[InboundItem] = []
    total = 0.0
    for item in items:
        product = db.get(Product, item.product_id)
        if not product:
            raise HTTPException(status_code=400, detail=f"商品 {item.product_id} 不存在")
        ensure_product_type(product, "raw_material", "入库单")
        amount = round(item.quantity * item.unit_price, 2)
        total += amount
        rows.append(
            InboundItem(
                product_id=item.product_id,
                quantity=item.quantity,
                unit_price=item.unit_price,
                amount=amount,
                batch_no=item.batch_no,
                expiry_date=item.expiry_date,
                remark=item.remark,
            )
        )
    return rows, round(total, 2)


@router.get("", summary="入库单列表")
def list_inbounds(
    keyword: str = Query(""),
    status: str = Query(""),
    supplier_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("inbound:view")),
):
    stmt = select(InboundOrder).join(Supplier)
    stmt = apply_owner_scope(stmt, InboundOrder.created_by, user)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            or_(
                InboundOrder.inbound_no.like(like),
                Supplier.name.like(like),
                InboundOrder.remark.like(like),
            )
        )
    if status:
        stmt = stmt.where(InboundOrder.status == status)
    if supplier_id:
        stmt = stmt.where(InboundOrder.supplier_id == supplier_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.options(selectinload(InboundOrder.items))
        .order_by(InboundOrder.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"total": total, "items": [_serialize(row) for row in rows]}


@router.get("/{inbound_id}", summary="入库单详情")
def get_inbound(
    inbound_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("inbound:view")),
):
    row = _get(db, inbound_id)
    ensure_owner(row.created_by, user)
    return _serialize(row)


def _inbound_docx_data(row: InboundOrder) -> tuple[dict, list[dict]]:
    supplier = row.supplier
    items = []
    total_quantity = 0
    for item in row.items:
        product = item.product
        total_quantity += item.quantity
        items.append(
            {
                "sku": product.sku if product else "",
                "name": product.name if product else "",
                "spec": product.spec if product else "",
                "unit": product.unit if product else "",
                "category": product.category if product else "",
                "quantity": item.quantity,
                "unit_price": money(item.unit_price),
                "amount": money(item.amount),
                "batch_no": item.batch_no or "",
                "remark": item.remark or "",
            }
        )
    values = {
        "order_no": row.inbound_no,
        "inbound_no": row.inbound_no,
        "order_date": row.receipt_date.strftime("%Y-%m-%d") if row.receipt_date else "",
        "receipt_date": row.receipt_date.strftime("%Y-%m-%d") if row.receipt_date else "",
        "status": row.status,
        "status_text": STATUS_TEXT.get(row.status, row.status),
        "created_by": row.created_by or "",
        "created_at": row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else "",
        "supplier_name": supplier.name if supplier else "",
        "supplier_contact": supplier.contact if supplier else "",
        "supplier_phone": supplier.phone if supplier else "",
        "supplier_email": supplier.email if supplier else "",
        "supplier_address": supplier.address if supplier else "",
        "supply_category": supplier.supply_category if supplier else "",
        "warehouse_name": row.warehouse.name if row.warehouse else "",
        "location_code": row.location.code if row.location else "",
        "purchase_order_no": row.purchase_order.order_no if row.purchase_order else "",
        "item_count": len(row.items),
        "total_quantity": total_quantity,
        "gross_amount": money(row.total_amount),
        "discount_amount": "0.00",
        "total_amount": money(row.total_amount),
        "remark": row.remark or "",
    }
    return values, items


@router.get("/{inbound_id}/print.docx", summary="生成入库单 Word 单据")
def print_inbound_docx(
    inbound_id: int,
    template_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("inbound:view")),
):
    row = _get(db, inbound_id)
    ensure_owner(row.created_by, user, "打印")
    values, items = _inbound_docx_data(row)
    return build_docx_download(
        db,
        doc_type="inbound",
        template_id=template_id,
        values=values,
        items=items,
        default_builder=build_inbound_document,
        default_title="入库单",
        doc_no=row.inbound_no,
    )


@router.post("/recognize", summary="上传入库单照片并识别")
async def recognize(
    request: Request,
    files: list[UploadFile] | None = File(None, description="单据照片，可多页"),
    file: UploadFile | None = File(None, description="兼容旧版的单张照片字段"),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("inbound:manage")),
):
    uploads = list(files or [])
    if file is not None:
        uploads.append(file)
    if not uploads:
        raise HTTPException(status_code=400, detail="请上传单据照片")
    return await recognize_uploaded(db, uploads, "inbound", request, user)


def _learn_aliases(db: Session, payload: InboundCreate, supplier: Supplier) -> None:
    """把用户确认过的「识别名 → 商品/供应商」记下来，下次识别直接命中。"""
    entries = [(item.product_id, item.raw_name) for item in payload.items if item.raw_name]
    if entries:
        names = matching.product_name_map(db, [product_id for product_id, _ in entries])
        matching.learn_item_aliases(db, entries, names, source="ocr")
    if payload.raw_supplier_name:
        matching.learn_alias(
            db,
            "supplier",
            supplier.id,
            payload.raw_supplier_name,
            canonical=supplier.name,
            source="ocr",
        )


@router.post("", summary="新建入库单草稿")
def create_inbound(
    payload: InboundCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("inbound:manage")),
):
    supplier = db.get(Supplier, payload.supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="供应商不存在")
    items, total = _build_items(db, payload.items)
    row = InboundOrder(
        inbound_no=gen_order_no(db, InboundOrder, "RK", field="inbound_no"),
        purchase_order_id=payload.purchase_order_id,
        supplier_id=payload.supplier_id,
        warehouse_id=payload.warehouse_id,
        location_id=payload.location_id,
        receipt_date=payload.receipt_date or datetime.now(),
        remark=payload.remark,
        photo_path=payload.photo_path,
        photo_name=payload.photo_name,
        photo_paths=json.dumps([page.model_dump() for page in payload.photo_paths], ensure_ascii=False),
        ocr_status=payload.ocr_status,
        ocr_raw=payload.ocr_raw,
        total_amount=total,
        created_by=user.username,
        items=items,
    )
    _learn_aliases(db, payload, supplier)
    db.add(row)
    db.commit()
    row = _get(db, row.id)
    record(
        username=user.username,
        role=user.role,
        action="create",
        resource="inbound",
        detail=f"新建入库单 {row.inbound_no}（{supplier.name}，{len(items)} 行）",
        request=request,
    )
    return _serialize(row)


@router.put("/{inbound_id}", summary="修改入库单草稿")
def update_inbound(
    inbound_id: int,
    payload: InboundCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("inbound:manage")),
):
    row = _get(db, inbound_id)
    ensure_owner(row.created_by, user)
    if row.status != "draft":
        raise HTTPException(status_code=400, detail="只有草稿状态的入库单可以修改")
    supplier = db.get(Supplier, payload.supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="供应商不存在")
    items, total = _build_items(db, payload.items)
    row.supplier_id = payload.supplier_id
    row.warehouse_id = payload.warehouse_id
    row.location_id = payload.location_id
    row.purchase_order_id = payload.purchase_order_id
    row.receipt_date = payload.receipt_date or row.receipt_date
    row.remark = payload.remark
    row.photo_path = payload.photo_path or row.photo_path
    row.photo_name = payload.photo_name or row.photo_name
    if payload.photo_paths:
        row.photo_paths = json.dumps(
            [page.model_dump() for page in payload.photo_paths], ensure_ascii=False
        )
    row.ocr_status = payload.ocr_status or row.ocr_status
    row.ocr_raw = payload.ocr_raw or row.ocr_raw
    row.total_amount = total
    row.items = items
    _learn_aliases(db, payload, supplier)
    db.commit()
    row = _get(db, inbound_id)
    record(
        username=user.username,
        role=user.role,
        action="update",
        resource="inbound",
        detail=f"修改入库单 {row.inbound_no}",
        request=request,
    )
    return _serialize(row)


@router.post("/{inbound_id}/status", summary="确认入库 / 取消入库单")
def change_status(
    inbound_id: int,
    payload: InboundAction,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("inbound:manage")),
):
    row = _get(db, inbound_id)
    ensure_owner(row.created_by, user)

    if payload.action == "confirm":
        if row.status != "draft":
            raise HTTPException(status_code=400, detail="只有草稿状态的入库单可以确认入库")
        for item in row.items:
            product = db.get(Product, item.product_id)
            if not product:
                raise HTTPException(status_code=400, detail=f"商品 {item.product_id} 不存在")
            ensure_product_type(product, "raw_material", "采购入库")
            apply_stock_change(
                db,
                product,
                "in",
                item.quantity,
                warehouse_id=row.warehouse_id,
                location_id=row.location_id,
                batch_no=item.batch_no or "",
                expiry_date=item.expiry_date,
                reason=payload.remark or row.remark,
                biz_type="入库单",
                ref_no=row.inbound_no,
                username=user.username,
                unit_cost=item.unit_price,
            )
        row.status = "confirmed"
        row.confirmed_at = datetime.now()
    elif payload.action == "cancel":
        if row.status == "cancelled":
            raise HTTPException(status_code=400, detail="入库单已取消")
        if row.status == "confirmed":
            for item in row.items:
                product = db.get(Product, item.product_id)
                if not product:
                    raise HTTPException(status_code=400, detail=f"商品 {item.product_id} 不存在")
                apply_stock_change(
                    db,
                    product,
                    "out",
                    item.quantity,
                    warehouse_id=row.warehouse_id,
                    location_id=row.location_id,
                    batch_no=item.batch_no or "",
                    reason="取消入库单回滚",
                    biz_type="入库单取消",
                    ref_no=row.inbound_no,
                    username=user.username,
                )
        row.status = "cancelled"
        row.cancelled_at = datetime.now()
    else:
        raise HTTPException(status_code=400, detail="不支持的操作")

    db.commit()
    row = _get(db, inbound_id)
    record(
        username=user.username,
        role=user.role,
        action=payload.action,
        resource="inbound",
        detail=f"{'确认入库' if payload.action == 'confirm' else '取消入库单'} {row.inbound_no}",
        request=request,
    )
    return _serialize(row)


@router.delete("/{inbound_id}", summary="删除入库单草稿")
def delete_inbound(
    inbound_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("inbound:manage")),
):
    row = _get(db, inbound_id)
    ensure_owner(row.created_by, user)
    if row.status != "draft":
        raise HTTPException(status_code=400, detail="只有草稿状态的入库单可以删除")
    no = row.inbound_no
    db.delete(row)
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="delete",
        resource="inbound",
        detail=f"删除入库单 {no}",
        request=request,
    )
    return {"ok": True}
