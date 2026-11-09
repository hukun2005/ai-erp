from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import require_perm
from ..models import BillOfMaterials, BomItem, Product, ProductionOrder, User
from ..schemas import BomIn
from ..services.product_type import ensure_product_type
from ..utils import gen_order_no

router = APIRouter(prefix="/api/boms", tags=["BOM 清单"])


def serialize_bom(bom: BillOfMaterials) -> dict:
    return {
        "id": bom.id,
        "bom_no": bom.bom_no,
        "product_id": bom.product_id,
        "product_name": bom.product.name if bom.product else "",
        "product_sku": bom.product.sku if bom.product else "",
        "product_type": bom.product.product_type if bom.product else "",
        "unit": bom.product.unit if bom.product else "",
        "name": bom.name,
        "version": bom.version,
        "base_qty": bom.base_qty,
        "status": bom.status,
        "remark": bom.remark,
        "created_by": bom.created_by,
        "created_at": bom.created_at,
        "item_count": len(bom.items),
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product.name if item.product else "",
                "product_sku": item.product.sku if item.product else "",
                "product_type": item.product.product_type if item.product else "",
                "unit": item.product.unit if item.product else "",
                "quantity": item.quantity,
                "cost_price": item.product.cost_price if item.product else 0.0,
                "remark": item.remark,
            }
            for item in bom.items
        ],
    }


def _get(db: Session, bom_id: int) -> BillOfMaterials:
    bom = db.get(BillOfMaterials, bom_id)
    if not bom:
        raise HTTPException(status_code=404, detail="BOM 不存在")
    return bom


def _validate_items(db: Session, items, product_id: int) -> None:
    seen: set[int] = set()
    for item in items:
        if item.product_id == product_id:
            raise HTTPException(status_code=400, detail="BOM 用料不能包含成品本身")
        if item.product_id in seen:
            raise HTTPException(status_code=400, detail="同一物料在 BOM 中重复出现")
        seen.add(item.product_id)
        material = db.get(Product, item.product_id)
        if not material:
            raise HTTPException(status_code=400, detail=f"物料 {item.product_id} 不存在")
        ensure_product_type(material, "raw_material", "BOM 用料")


@router.get("", summary="BOM 列表")
def list_boms(
    keyword: str = Query(""),
    status: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("bom:view")),
):
    stmt = select(BillOfMaterials).join(Product, Product.id == BillOfMaterials.product_id)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            or_(BillOfMaterials.bom_no.like(like), BillOfMaterials.name.like(like), Product.name.like(like))
        )
    if status:
        stmt = stmt.where(BillOfMaterials.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(BillOfMaterials.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"total": total, "items": [serialize_bom(row) for row in rows]}


@router.get("/active/{product_id}", summary="成品当前启用的 BOM")
def active_bom_for(
    product_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("bom:view")),
):
    bom = db.scalar(
        select(BillOfMaterials)
        .where(BillOfMaterials.product_id == product_id)
        .where(BillOfMaterials.status == "active")
        .order_by(BillOfMaterials.id.desc())
    )
    return serialize_bom(bom) if bom else None


@router.get("/{bom_id}", summary="BOM 详情")
def get_bom(bom_id: int, db: Session = Depends(get_db), _: User = Depends(require_perm("bom:view"))):
    return serialize_bom(_get(db, bom_id))


@router.post("", summary="新建 BOM", dependencies=[Depends(require_perm("bom:manage"))])
def create_bom(
    payload: BomIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("bom:manage")),
):
    product = db.get(Product, payload.product_id)
    if not product:
        raise HTTPException(status_code=400, detail="成品不存在")
    ensure_product_type(product, "finished_good", "BOM 成品")
    _validate_items(db, payload.items, payload.product_id)

    bom = BillOfMaterials(
        bom_no=gen_order_no(db, BillOfMaterials, "BOM", field="bom_no"),
        product_id=payload.product_id,
        name=payload.name or f"{product.name} BOM",
        version=payload.version,
        base_qty=payload.base_qty,
        status=payload.status,
        remark=payload.remark,
        created_by=user.username,
    )
    for item in payload.items:
        bom.items.append(
            BomItem(product_id=item.product_id, quantity=item.quantity, remark=item.remark)
        )
    db.add(bom)
    db.commit()
    db.refresh(bom)
    record(
        username=user.username,
        role=user.role,
        action="新建 BOM",
        resource=f"BOM {bom.bom_no}",
        detail=f"成品 {product.name}，用料 {len(bom.items)} 行",
        request=request,
    )
    return serialize_bom(bom)


@router.put("/{bom_id}", summary="编辑 BOM", dependencies=[Depends(require_perm("bom:manage"))])
def update_bom(
    bom_id: int,
    payload: BomIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("bom:manage")),
):
    bom = _get(db, bom_id)
    product = db.get(Product, payload.product_id)
    if not product:
        raise HTTPException(status_code=400, detail="成品不存在")
    ensure_product_type(product, "finished_good", "BOM 成品")
    _validate_items(db, payload.items, payload.product_id)

    bom.product_id = payload.product_id
    bom.name = payload.name or bom.name
    bom.version = payload.version
    bom.base_qty = payload.base_qty
    bom.status = payload.status
    bom.remark = payload.remark
    bom.items.clear()
    for item in payload.items:
        bom.items.append(
            BomItem(product_id=item.product_id, quantity=item.quantity, remark=item.remark)
        )
    db.commit()
    db.refresh(bom)
    record(
        username=user.username,
        role=user.role,
        action="编辑 BOM",
        resource=f"BOM {bom.bom_no}",
        detail=f"成品 {product.name}，用料 {len(bom.items)} 行",
        request=request,
    )
    return serialize_bom(bom)


@router.delete("/{bom_id}", summary="删除 BOM", dependencies=[Depends(require_perm("bom:manage"))])
def delete_bom(
    bom_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("bom:manage")),
):
    bom = _get(db, bom_id)
    used = db.scalar(
        select(func.count()).select_from(ProductionOrder).where(ProductionOrder.bom_id == bom_id)
    )
    if used:
        raise HTTPException(status_code=400, detail="该 BOM 已被生产订单引用，不能删除，可改为停用")
    bom_no = bom.bom_no
    db.delete(bom)
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="删除 BOM",
        resource=f"BOM {bom_no}",
        request=request,
    )
    return {"ok": True}
