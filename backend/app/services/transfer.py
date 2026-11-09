"""库存调拨服务：多仓调拨单的状态流转与在途库存统计。

调拨不动商品总库存，只改变分布：
- 发出：源仓库位出库，流水记「调拨出库」，单据进入在途（在途数量 = 未到达确认的调拨数量）；
- 到达确认：目标仓库位入库，流水记「调拨入库」，在途清零；
- 取消：草稿直接作废；在途取消把已发出的数量退回源库位，流水记「调拨退回」。
"""
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from ..models import Product, StorageLocation, TransferItem, TransferOrder, Warehouse
from .stock import apply_stock_change

STATUS_LABELS = {
    "draft": "草稿",
    "in_transit": "在途",
    "completed": "已完成",
    "cancelled": "已取消",
}


def gen_no(db: Session) -> str:
    """生成形如 TR202611010001 的调拨单号。"""
    today = datetime.now().strftime("%Y%m%d")
    like = f"TR{today}%"
    count = db.scalar(
        select(func.count()).select_from(TransferOrder).where(TransferOrder.transfer_no.like(like))
    ) or 0
    return f"TR{today}{count + 1:04d}"


def _check_location(db: Session, location_id: int | None, warehouse: Warehouse, label: str) -> None:
    if location_id is None:
        return
    location = db.get(StorageLocation, location_id)
    if not location or location.warehouse_id != warehouse.id:
        raise HTTPException(status_code=400, detail=f"{label}库位不属于所选仓库")
    if not location.is_active:
        raise HTTPException(status_code=400, detail=f"{label}库位「{location.code}」已停用")


def create_order(
    db: Session,
    *,
    from_warehouse_id: int,
    to_warehouse_id: int,
    items: list,
    remark: str,
    username: str,
) -> TransferOrder:
    """创建调拨草稿，校验仓库、商品与库位归属。"""
    if from_warehouse_id == to_warehouse_id:
        raise HTTPException(status_code=400, detail="调出仓库与调入仓库不能相同")
    from_warehouse = db.get(Warehouse, from_warehouse_id)
    to_warehouse = db.get(Warehouse, to_warehouse_id)
    if not from_warehouse or not to_warehouse:
        raise HTTPException(status_code=404, detail="仓库不存在")
    if not from_warehouse.is_active or not to_warehouse.is_active:
        raise HTTPException(status_code=400, detail="调拨涉及的仓库已停用")
    if not items:
        raise HTTPException(status_code=400, detail="调拨明细不能为空")

    order = TransferOrder(
        transfer_no=gen_no(db),
        from_warehouse_id=from_warehouse_id,
        to_warehouse_id=to_warehouse_id,
        status="draft",
        remark=remark,
        created_by=username,
    )
    for item in items:
        product = db.get(Product, item.product_id)
        if not product or not product.is_active:
            raise HTTPException(status_code=400, detail="调拨商品不存在或已停用")
        if item.quantity <= 0:
            raise HTTPException(status_code=400, detail="调拨数量必须大于 0")
        _check_location(db, item.from_location_id, from_warehouse, "调出")
        _check_location(db, item.to_location_id, to_warehouse, "调入")
        order.items.append(
            TransferItem(
                product_id=item.product_id,
                quantity=item.quantity,
                from_location_id=item.from_location_id,
                to_location_id=item.to_location_id,
            )
        )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def send(db: Session, order: TransferOrder, username: str) -> list[int]:
    """发出调拨：源仓逐库位扣减库存，返回涉及的商品 id 供自动预警检查。"""
    if order.status != "draft":
        raise HTTPException(status_code=400, detail="只有草稿状态的调拨单可以发出")
    affected: list[int] = []
    for item in order.items:
        product = db.get(Product, item.product_id)
        if not product:
            raise HTTPException(status_code=400, detail="调拨商品不存在")
        apply_stock_change(
            db,
            product,
            "out",
            item.quantity,
            warehouse_id=order.from_warehouse_id,
            location_id=item.from_location_id,
            biz_type="调拨出库",
            ref_no=order.transfer_no,
            reason=f"调拨至 {order.to_warehouse.name}",
            username=username,
        )
        if item.product_id not in affected:
            affected.append(item.product_id)
    order.status = "in_transit"
    order.sent_at = datetime.now()
    db.commit()
    db.refresh(order)
    return affected


def complete(db: Session, order: TransferOrder, username: str) -> None:
    """到达确认：目标仓逐库位入库，在途结束。"""
    if order.status != "in_transit":
        raise HTTPException(status_code=400, detail="只有在途状态的调拨单可以确认到达")
    for item in order.items:
        product = db.get(Product, item.product_id)
        if not product:
            raise HTTPException(status_code=400, detail="调拨商品不存在")
        apply_stock_change(
            db,
            product,
            "in",
            item.quantity,
            warehouse_id=order.to_warehouse_id,
            location_id=item.to_location_id,
            biz_type="调拨入库",
            ref_no=order.transfer_no,
            reason=f"由 {order.from_warehouse.name} 调入",
            username=username,
        )
    order.status = "completed"
    order.completed_at = datetime.now()
    db.commit()
    db.refresh(order)


def cancel(db: Session, order: TransferOrder, username: str) -> None:
    """取消调拨：草稿直接作废；在途需把已发出数量退回源库位。"""
    if order.status not in ("draft", "in_transit"):
        raise HTTPException(status_code=400, detail="当前状态不支持取消")
    if order.status == "in_transit":
        for item in order.items:
            product = db.get(Product, item.product_id)
            if not product:
                raise HTTPException(status_code=400, detail="调拨商品不存在")
            apply_stock_change(
                db,
                product,
                "in",
                item.quantity,
                warehouse_id=order.from_warehouse_id,
                location_id=item.from_location_id,
                biz_type="调拨退回",
                ref_no=order.transfer_no,
                reason=f"调拨取消，退回自 {order.to_warehouse.name}",
                username=username,
            )
    order.status = "cancelled"
    db.commit()
    db.refresh(order)


def in_transit_map(db: Session) -> dict[int, int]:
    """各商品当前在途数量（未到达确认的调拨数量合计）。"""
    rows = db.execute(
        select(TransferItem.product_id, func.sum(TransferItem.quantity))
        .join(TransferOrder, TransferOrder.id == TransferItem.transfer_id)
        .where(TransferOrder.status == "in_transit")
        .group_by(TransferItem.product_id)
    ).all()
    return {pid: int(qty or 0) for pid, qty in rows}


def stats(db: Session) -> dict:
    orders = db.scalar(
        select(func.count()).select_from(TransferOrder).where(TransferOrder.status == "in_transit")
    ) or 0
    qty = db.scalar(
        select(func.sum(TransferItem.quantity))
        .join(TransferOrder, TransferOrder.id == TransferItem.transfer_id)
        .where(TransferOrder.status == "in_transit")
    ) or 0
    return {"in_transit_orders": int(orders), "in_transit_qty": int(qty)}


def _location_names(db: Session) -> dict[int, str]:
    return {loc.id: loc.code for loc in db.scalars(select(StorageLocation)).all()}


def serialize(order: TransferOrder, locations: dict[int, str] | None = None) -> dict:
    """输出调拨单结构，列表与详情共用。"""
    names = locations or {}
    return {
        "id": order.id,
        "transfer_no": order.transfer_no,
        "from_warehouse_id": order.from_warehouse_id,
        "from_warehouse_name": order.from_warehouse.name if order.from_warehouse else "",
        "to_warehouse_id": order.to_warehouse_id,
        "to_warehouse_name": order.to_warehouse.name if order.to_warehouse else "",
        "status": order.status,
        "status_label": STATUS_LABELS.get(order.status, order.status),
        "remark": order.remark,
        "created_by": order.created_by,
        "created_at": order.created_at,
        "sent_at": order.sent_at,
        "completed_at": order.completed_at,
        "total_qty": sum(item.quantity for item in order.items),
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_sku": item.product.sku if item.product else "",
                "product_name": item.product.name if item.product else "",
                "unit": item.product.unit if item.product else "",
                "quantity": item.quantity,
                "from_location_id": item.from_location_id,
                "from_location_code": names.get(item.from_location_id or 0, ""),
                "to_location_id": item.to_location_id,
                "to_location_code": names.get(item.to_location_id or 0, ""),
            }
            for item in order.items
        ],
    }


def build_query(keyword: str, status: str):
    """调拨单查询条件：单号、备注、调出/调入仓库名关键字。"""
    from_w = aliased(Warehouse)
    to_w = aliased(Warehouse)
    stmt = (
        select(TransferOrder)
        .join(from_w, TransferOrder.from_warehouse_id == from_w.id)
        .join(to_w, TransferOrder.to_warehouse_id == to_w.id)
    )
    keyword = (keyword or "").strip()
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            or_(
                TransferOrder.transfer_no.like(like),
                TransferOrder.remark.like(like),
                from_w.name.like(like),
                to_w.name.like(like),
            )
        )
    if status:
        stmt = stmt.where(TransferOrder.status == status)
    return stmt
