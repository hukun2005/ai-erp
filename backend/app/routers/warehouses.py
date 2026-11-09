"""仓库与库位主数据管理（仅管理员维护，仓管可读下拉选项）。"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import require_any_perm, require_perm
from ..models import StockBalance, StorageLocation, User, Warehouse
from ..schemas import LocationIn, LocationUpdate, WarehouseIn, WarehouseUpdate

router = APIRouter(prefix="/api/warehouses", tags=["仓库与库位"])


def _serialize_location(location: StorageLocation, quantity: int = 0) -> dict:
    return {
        "id": location.id,
        "warehouse_id": location.warehouse_id,
        "code": location.code,
        "name": location.name,
        "is_active": location.is_active,
        "stock_qty": int(quantity),
    }


def _stock_map(db: Session, warehouse_id: int | None = None) -> dict:
    stmt = select(StockBalance.location_id, func.sum(StockBalance.quantity))
    if warehouse_id:
        stmt = stmt.where(StockBalance.warehouse_id == warehouse_id)
    return {loc_id: int(qty or 0) for loc_id, qty in db.execute(stmt.group_by(StockBalance.location_id)).all()}


@router.get("/options", summary="仓库/库位下拉选项（含库位）", dependencies=[Depends(require_any_perm("inventory:view", "transfer:view"))])
def warehouse_options(db: Session = Depends(get_db)):
    rows = db.scalars(select(Warehouse).order_by(Warehouse.is_default.desc(), Warehouse.id)).all()
    return [
        {
            "id": w.id,
            "code": w.code,
            "name": w.name,
            "is_default": w.is_default,
            "is_active": w.is_active,
            "locations": [
                {"id": loc.id, "code": loc.code, "name": loc.name, "is_active": loc.is_active}
                for loc in sorted(w.locations, key=lambda x: x.id)
            ],
        }
        for w in rows
    ]


@router.get(
    "",
    summary="仓库列表",
    dependencies=[Depends(require_any_perm("warehouse:manage", "inventory:view", "stocktake:view", "return:manage"))],
)
def list_warehouses(
    keyword: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = select(Warehouse)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(Warehouse.name.like(like) | Warehouse.code.like(like))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Warehouse.is_default.desc(), Warehouse.id)
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    stock = _stock_map(db)
    return {
        "total": total,
        "items": [
            {
                "id": w.id,
                "code": w.code,
                "name": w.name,
                "address": w.address,
                "manager": w.manager,
                "is_default": w.is_default,
                "is_active": w.is_active,
                "location_count": len(w.locations),
                "stock_qty": sum(stock.get(loc.id, 0) for loc in w.locations),
                "locations": [
                    _serialize_location(loc, stock.get(loc.id, 0))
                    for loc in sorted(w.locations, key=lambda x: x.id)
                ],
            }
            for w in rows
        ],
    }


def _clear_default(db: Session, exclude_id: int = 0) -> None:
    others = db.scalars(select(Warehouse).where(Warehouse.is_default == True)).all()  # noqa: E712
    for w in others:
        if w.id != exclude_id:
            w.is_default = False


@router.post("", summary="新建仓库", dependencies=[Depends(require_perm("warehouse:manage"))])
def create_warehouse(
    payload: WarehouseIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("warehouse:manage")),
):
    code = payload.code.strip().upper()
    if db.scalar(select(Warehouse).where(Warehouse.code == code)):
        raise HTTPException(status_code=400, detail=f"仓库编码 {code} 已存在")
    warehouse = Warehouse(
        code=code,
        name=payload.name.strip(),
        address=payload.address,
        manager=payload.manager,
        is_default=payload.is_default,
        is_active=payload.is_active,
    )
    if warehouse.is_default:
        _clear_default(db)
    elif db.scalar(select(func.count()).select_from(Warehouse)) == 0:
        warehouse.is_default = True  # 首个仓库自动设为默认
    db.add(warehouse)
    db.commit()
    db.refresh(warehouse)
    record(
        username=user.username,
        role=user.role,
        action="新建仓库",
        resource=f"仓库 {warehouse.code} {warehouse.name}",
        detail=f"默认仓库：{'是' if warehouse.is_default else '否'}，状态：{'启用' if warehouse.is_active else '停用'}",
        request=request,
    )
    return {"id": warehouse.id, "ok": True}


@router.put("/{warehouse_id}", summary="更新仓库", dependencies=[Depends(require_perm("warehouse:manage"))])
def update_warehouse(
    warehouse_id: int,
    payload: WarehouseUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("warehouse:manage")),
):
    warehouse = db.get(Warehouse, warehouse_id)
    if not warehouse:
        raise HTTPException(status_code=404, detail="仓库不存在")

    if payload.code is not None:
        code = payload.code.strip().upper()
        exists = db.scalar(
            select(Warehouse).where(Warehouse.code == code).where(Warehouse.id != warehouse_id)
        )
        if exists:
            raise HTTPException(status_code=400, detail=f"仓库编码 {code} 已存在")
        warehouse.code = code
    if payload.name is not None:
        warehouse.name = payload.name.strip()
    if payload.address is not None:
        warehouse.address = payload.address
    if payload.manager is not None:
        warehouse.manager = payload.manager
    if payload.is_active is not None:
        warehouse.is_active = payload.is_active
    if payload.is_default is not None:
        if payload.is_default:
            _clear_default(db, exclude_id=warehouse.id)
            warehouse.is_default = True
        elif warehouse.is_default:
            has_other = db.scalar(
                select(func.count()).select_from(Warehouse)
                .where(Warehouse.id != warehouse.id)
            ) or 0
            if has_other:
                raise HTTPException(
                    status_code=400, detail="请先将其他仓库设为默认仓库，系统需要保留一个默认仓库"
                )
            raise HTTPException(status_code=400, detail="至少需要保留一个默认仓库")

    db.commit()
    db.refresh(warehouse)
    record(
        username=user.username,
        role=user.role,
        action="更新仓库",
        resource=f"仓库 {warehouse.code} {warehouse.name}",
        detail=f"默认仓库：{'是' if warehouse.is_default else '否'}，状态：{'启用' if warehouse.is_active else '停用'}",
        request=request,
    )
    return {"ok": True}


@router.delete("/{warehouse_id}", summary="删除仓库", dependencies=[Depends(require_perm("warehouse:manage"))])
def delete_warehouse(
    warehouse_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("warehouse:manage")),
):
    warehouse = db.get(Warehouse, warehouse_id)
    if not warehouse:
        raise HTTPException(status_code=404, detail="仓库不存在")
    if warehouse.is_default:
        raise HTTPException(status_code=400, detail="默认仓库不能删除，请先将其他仓库设为默认")
    remain = db.scalar(
        select(func.sum(StockBalance.quantity)).where(StockBalance.warehouse_id == warehouse_id)
    ) or 0
    if remain > 0:
        raise HTTPException(
            status_code=400, detail=f"仓库下仍有 {int(remain)} 件库存，请先调拨或清空后再删除"
        )
    name = f"{warehouse.code} {warehouse.name}"
    db.delete(warehouse)
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="删除仓库",
        resource=f"仓库 {name}",
        detail="无库存残留，连同库位一并删除",
        request=request,
    )
    return {"ok": True}


def _get_location(db: Session, warehouse: Warehouse, location_id: int) -> StorageLocation:
    location = db.get(StorageLocation, location_id)
    if not location or location.warehouse_id != warehouse.id:
        raise HTTPException(status_code=404, detail="库位不存在")
    return location


@router.post("/{warehouse_id}/locations", summary="新建库位", dependencies=[Depends(require_perm("warehouse:manage"))])
def create_location(
    warehouse_id: int,
    payload: LocationIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("warehouse:manage")),
):
    warehouse = db.get(Warehouse, warehouse_id)
    if not warehouse:
        raise HTTPException(status_code=404, detail="仓库不存在")
    code = payload.code.strip().upper()
    exists = db.scalar(
        select(StorageLocation)
        .where(StorageLocation.warehouse_id == warehouse_id)
        .where(StorageLocation.code == code)
    )
    if exists:
        raise HTTPException(status_code=400, detail=f"该仓库下库位编码 {code} 已存在")
    location = StorageLocation(
        warehouse_id=warehouse_id, code=code, name=payload.name, is_active=payload.is_active
    )
    db.add(location)
    db.commit()
    db.refresh(location)
    record(
        username=user.username,
        role=user.role,
        action="新建库位",
        resource=f"库位 {warehouse.code}/{location.code}",
        detail=f"仓库：{warehouse.name}；库位名称：{location.name or '-'}",
        request=request,
    )
    return {"id": location.id, "ok": True}


@router.put("/{warehouse_id}/locations/{location_id}", summary="更新库位", dependencies=[Depends(require_perm("warehouse:manage"))])
def update_location(
    warehouse_id: int,
    location_id: int,
    payload: LocationUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("warehouse:manage")),
):
    warehouse = db.get(Warehouse, warehouse_id)
    if not warehouse:
        raise HTTPException(status_code=404, detail="仓库不存在")
    location = _get_location(db, warehouse, location_id)

    if payload.code is not None:
        code = payload.code.strip().upper()
        exists = db.scalar(
            select(StorageLocation)
            .where(StorageLocation.warehouse_id == warehouse_id)
            .where(StorageLocation.code == code)
            .where(StorageLocation.id != location_id)
        )
        if exists:
            raise HTTPException(status_code=400, detail=f"该仓库下库位编码 {code} 已存在")
        location.code = code
    if payload.name is not None:
        location.name = payload.name
    if payload.is_active is not None:
        location.is_active = payload.is_active
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="更新库位",
        resource=f"库位 {warehouse.code}/{location.code}",
        detail=f"仓库：{warehouse.name}；状态：{'启用' if location.is_active else '停用'}",
        request=request,
    )
    return {"ok": True}


@router.delete("/{warehouse_id}/locations/{location_id}", summary="删除库位", dependencies=[Depends(require_perm("warehouse:manage"))])
def delete_location(
    warehouse_id: int,
    location_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("warehouse:manage")),
):
    warehouse = db.get(Warehouse, warehouse_id)
    if not warehouse:
        raise HTTPException(status_code=404, detail="仓库不存在")
    location = _get_location(db, warehouse, location_id)
    remain = db.scalar(
        select(func.sum(StockBalance.quantity)).where(StockBalance.location_id == location_id)
    ) or 0
    if remain > 0:
        raise HTTPException(
            status_code=400, detail=f"库位仍有 {int(remain)} 件库存，请先移库后再删除"
        )
    code = location.code
    db.delete(location)
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="删除库位",
        resource=f"库位 {warehouse.code}/{code}",
        detail=f"仓库：{warehouse.name}",
        request=request,
    )
    return {"ok": True}


@router.get("/{warehouse_id}/locations", summary="仓库下的库位", dependencies=[Depends(require_perm("inventory:view"))])
def list_locations(warehouse_id: int, db: Session = Depends(get_db)):
    warehouse = db.get(Warehouse, warehouse_id)
    if not warehouse:
        raise HTTPException(status_code=404, detail="仓库不存在")
    stock = _stock_map(db, warehouse_id)
    return [
        _serialize_location(loc, stock.get(loc.id, 0))
        for loc in sorted(warehouse.locations, key=lambda x: x.id)
    ]
