from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import require_perm
from ..models import Product, StockAdjustment, StockAdjustmentItem, User, Warehouse
from ..schemas import AdjustmentAction, AdjustmentCreate
from ..services.stock import apply_stock_change
from ..utils import gen_order_no

router = APIRouter(prefix="/api/stock-adjustments", tags=["库存调整"])

TYPE_NAMES = {"gain": "盘盈入库", "loss": "盘亏出库", "cost": "成本调整"}


def serialize_adjustment(doc: StockAdjustment) -> dict:
    return {
        "id": doc.id,
        "adjust_no": doc.adjust_no,
        "adjust_type": doc.adjust_type,
        "adjust_type_name": TYPE_NAMES.get(doc.adjust_type, doc.adjust_type),
        "warehouse_id": doc.warehouse_id,
        "warehouse_name": doc.warehouse.name if doc.warehouse else "",
        "status": doc.status,
        "total_amount": doc.total_amount,
        "reason": doc.reason,
        "source_type": doc.source_type,
        "source_no": doc.source_no,
        "created_by": doc.created_by,
        "created_at": doc.created_at,
        "posted_at": doc.posted_at,
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product.name if item.product else "",
                "product_sku": item.product.sku if item.product else "",
                "unit": item.product.unit if item.product else "",
                "quantity": item.quantity,
                "unit_cost": item.unit_cost,
                "amount": item.amount,
                "stock_qty": item.product.stock_qty if item.product else 0,
            }
            for item in doc.items
        ],
    }


def _get(db: Session, doc_id: int) -> StockAdjustment:
    doc = db.get(StockAdjustment, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="调整单不存在")
    return doc


@router.get("", summary="库存调整单列表")
def list_adjustments(
    keyword: str = Query(""),
    adjust_type: str = Query(""),
    status: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("inventory:view")),
):
    stmt = select(StockAdjustment)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(StockAdjustment.adjust_no.like(like), StockAdjustment.reason.like(like)))
    if adjust_type:
        stmt = stmt.where(StockAdjustment.adjust_type == adjust_type)
    if status:
        stmt = stmt.where(StockAdjustment.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(StockAdjustment.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"total": total, "items": [serialize_adjustment(row) for row in rows]}


@router.get("/{doc_id}", summary="库存调整单详情")
def get_adjustment(
    doc_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("inventory:view")),
):
    return serialize_adjustment(_get(db, doc_id))


@router.post("", summary="新建库存调整单", dependencies=[Depends(require_perm("inventory:adjust"))])
def create_adjustment(
    payload: AdjustmentCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("inventory:adjust")),
):
    warehouse = db.get(Warehouse, payload.warehouse_id)
    if not warehouse:
        raise HTTPException(status_code=400, detail="仓库不存在")
    if not payload.items:
        raise HTTPException(status_code=400, detail="调整明细不能为空")

    doc = StockAdjustment(
        adjust_no=gen_order_no(db, StockAdjustment, "ADJ", field="adjust_no"),
        adjust_type=payload.adjust_type,
        warehouse_id=warehouse.id,
        status="draft",
        reason=payload.reason,
        created_by=user.username,
    )
    total = 0.0
    for entry in payload.items:
        product = db.get(Product, entry.product_id)
        if not product:
            raise HTTPException(status_code=400, detail=f"商品 {entry.product_id} 不存在")
        if payload.adjust_type == "cost":
            if entry.unit_cost <= 0:
                raise HTTPException(status_code=400, detail=f"{product.name} 的调整后成本必须大于 0")
            amount = round((entry.unit_cost - product.cost_price) * product.stock_qty, 2)
        else:
            if entry.quantity <= 0:
                raise HTTPException(status_code=400, detail=f"{product.name} 的调整数量必须大于 0")
            amount = round(entry.quantity * product.cost_price, 2)
        total += amount
        doc.items.append(
            StockAdjustmentItem(
                product_id=entry.product_id,
                quantity=entry.quantity if payload.adjust_type != "cost" else 0,
                unit_cost=entry.unit_cost,
                amount=amount,
            )
        )
    doc.total_amount = round(total, 2)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    record(
        username=user.username,
        role=user.role,
        action="新建库存调整单",
        resource=f"{TYPE_NAMES[doc.adjust_type]} {doc.adjust_no}",
        detail=f"仓库 {warehouse.name}，明细 {len(doc.items)} 行",
        request=request,
    )
    return serialize_adjustment(doc)


@router.post("/{doc_id}/status", summary="过账 / 取消库存调整单")
def change_status(
    doc_id: int,
    payload: AdjustmentAction,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("inventory:adjust")),
):
    doc = _get(db, doc_id)
    action = payload.action

    if action == "post":
        if doc.status != "draft":
            raise HTTPException(status_code=400, detail="只有草稿调整单可以过账")
        total = 0.0
        for item in doc.items:
            if doc.adjust_type == "gain":
                apply_stock_change(
                    db,
                    item.product,
                    "in",
                    item.quantity,
                    warehouse_id=doc.warehouse_id,
                    reason=f"{doc.adjust_no} 盘盈入库",
                    biz_type="盘盈入库",
                    ref_no=doc.adjust_no,
                    username=user.username,
                )
                total += item.quantity * item.product.cost_price
            elif doc.adjust_type == "loss":
                apply_stock_change(
                    db,
                    item.product,
                    "out",
                    item.quantity,
                    warehouse_id=doc.warehouse_id,
                    reason=f"{doc.adjust_no} 盘亏出库",
                    biz_type="盘亏出库",
                    ref_no=doc.adjust_no,
                    username=user.username,
                )
                total -= item.quantity * item.product.cost_price
            else:
                total += (item.unit_cost - item.product.cost_price) * item.product.stock_qty
                item.product.cost_price = item.unit_cost
        doc.total_amount = round(total, 2)
        doc.status = "posted"
        doc.posted_at = datetime.now()

    elif action == "cancel":
        if doc.status != "draft":
            raise HTTPException(status_code=400, detail="只有草稿调整单可以取消；已过账单请用反向单据冲回")
        doc.status = "cancelled"
        doc.cancelled_at = datetime.now()

    else:
        raise HTTPException(status_code=400, detail=f"不支持的操作：{action}")

    db.commit()
    db.refresh(doc)
    record(
        username=user.username,
        role=user.role,
        action="库存调整单状态变更",
        resource=f"{TYPE_NAMES[doc.adjust_type]} {doc.adjust_no}",
        detail="过账" if action == "post" else "取消",
        request=request,
    )
    return serialize_adjustment(doc)


@router.delete("/{doc_id}", summary="删除草稿库存调整单")
def delete_adjustment(
    doc_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("inventory:adjust")),
):
    doc = _get(db, doc_id)
    if doc.status != "draft":
        raise HTTPException(status_code=400, detail="只有草稿调整单可以删除")
    db.delete(doc)
    db.commit()
    return {"ok": True}
