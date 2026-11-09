"""库存调拨：多仓调拨单的创建、发出、到达确认与取消。"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import require_perm
from ..models import Product, StockBalance, StorageLocation, TransferOrder, User
from ..schemas import TransferActionIn, TransferIn
from ..services import alerts, transfer

router = APIRouter(prefix="/api/transfers", tags=["库存调拨"])

ACTION_LABELS = {"send": "发出调拨", "complete": "确认到达", "cancel": "取消调拨"}


def _get_order(db: Session, order_id: int) -> TransferOrder:
    order = db.get(TransferOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="调拨单不存在")
    return order


@router.get("", summary="调拨单列表", dependencies=[Depends(require_perm("transfer:view"))])
def list_transfers(
    keyword: str = Query(""),
    status: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = transfer.build_query(keyword, status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(TransferOrder.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    locations = transfer._location_names(db)
    return {
        "total": total,
        "items": [transfer.serialize(row, locations) for row in rows],
        **transfer.stats(db),
    }


@router.get("/stats", summary="在途库存统计", dependencies=[Depends(require_perm("transfer:view"))])
def transfer_stats(db: Session = Depends(get_db)):
    return transfer.stats(db)


@router.get("/source-stock", summary="调出仓库的可调库存（商品 × 库位）", dependencies=[Depends(require_perm("transfer:view"))])
def source_stock(
    warehouse_id: int = Query(...),
    keyword: str = Query(""),
    db: Session = Depends(get_db),
):
    """新建调拨单时选商品用：只返回该仓有货的商品与库位数量。"""
    stmt = (
        select(StockBalance, Product, StorageLocation)
        .join(Product, Product.id == StockBalance.product_id)
        .join(StorageLocation, StorageLocation.id == StockBalance.location_id)
        .where(StockBalance.warehouse_id == warehouse_id)
        .where(StockBalance.quantity > 0)
        .where(Product.is_active == True)  # noqa: E712
    )
    if keyword.strip():
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(Product.name.like(like) | Product.sku.like(like))
    rows = db.execute(
        stmt.order_by(Product.sku, StorageLocation.code)
    ).all()
    return [
        {
            "product_id": product.id,
            "product_sku": product.sku,
            "product_name": product.name,
            "unit": product.unit,
            "location_id": location.id,
            "location_code": location.code,
            "quantity": int(balance.quantity),
        }
        for balance, product, location in rows
    ]


@router.get("/{order_id}", summary="调拨单详情", dependencies=[Depends(require_perm("transfer:view"))])
def get_transfer(
    order_id: int,
    db: Session = Depends(get_db),
):
    order = _get_order(db, order_id)
    return transfer.serialize(order, transfer._location_names(db))


@router.post("", summary="新建调拨单（草稿）", dependencies=[Depends(require_perm("transfer:manage"))])
def create_transfer(
    payload: TransferIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("transfer:manage")),
):
    order = transfer.create_order(
        db,
        from_warehouse_id=payload.from_warehouse_id,
        to_warehouse_id=payload.to_warehouse_id,
        items=payload.items,
        remark=payload.remark,
        username=user.username,
    )
    record(
        username=user.username,
        role=user.role,
        action="创建调拨单",
        resource=f"调拨单 {order.transfer_no}",
        detail=(
            f"{order.from_warehouse.name} → {order.to_warehouse.name}，"
            f"共 {len(order.items)} 个商品 / {sum(i.quantity for i in order.items)} 件"
        ),
        request=request,
    )
    return transfer.serialize(order, transfer._location_names(db))


@router.post("/{order_id}/status", summary="调拨单流转（发出/到达确认/取消）")
def change_status(
    order_id: int,
    payload: TransferActionIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("transfer:manage")),
):
    order = _get_order(db, order_id)
    action = payload.action
    previous = order.status
    auto_alert = None

    if action == "send":
        affected = transfer.send(db, order, user.username)
        # 源仓库存减少，复用库存自动预警规则（未启用或未接入飞书时静默跳过）
        auto_alert = alerts.check_and_notify(
            db, affected, user.username, scene=f"调拨出库 {order.transfer_no}"
        )
    elif action == "complete":
        transfer.complete(db, order, user.username)
    else:
        transfer.cancel(db, order, user.username)

    record(
        username=user.username,
        role=user.role,
        action="调拨单状态变更",
        resource=f"调拨单 {order.transfer_no}",
        detail=(
            f"{ACTION_LABELS[action]}：{order.from_warehouse.name} → {order.to_warehouse.name}，"
            f"状态 {transfer.STATUS_LABELS.get(previous, previous)} → "
            f"{transfer.STATUS_LABELS.get(order.status, order.status)}"
        ),
        request=request,
    )
    data = transfer.serialize(order, transfer._location_names(db))
    if auto_alert is not None:
        data["auto_alert"] = auto_alert
    return data


@router.delete("/{order_id}", summary="删除草稿调拨单", dependencies=[Depends(require_perm("transfer:manage"))])
def delete_transfer(
    order_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("transfer:manage")),
):
    order = _get_order(db, order_id)
    if order.status != "draft":
        raise HTTPException(status_code=400, detail="只有草稿状态的调拨单可以删除")
    no = order.transfer_no
    summary = f"{order.from_warehouse.name} → {order.to_warehouse.name}"
    db.delete(order)
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="删除调拨单",
        resource=f"调拨单 {no}",
        detail=f"删除草稿调拨单：{summary}",
        request=request,
    )
    return {"ok": True}
