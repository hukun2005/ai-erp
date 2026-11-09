"""库存服务：按仓库/库位维护现存量，并统一写入库存流水台账。

所有会改变库存的动作（手工出入库、采购入库、销售出库、单据撤销）都应走这里，
保证 StockBalance（库位余额）、Product.stock_qty（商品总库存）与 StockLog（流水）三者一致。
"""
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Product, StockBalance, StockLog, StorageLocation, Warehouse

CHANGE_TYPE_NAMES = {"in": "入库", "out": "出库", "check": "盘点"}


def default_warehouse(db: Session) -> Warehouse:
    """取默认仓库；没有显式默认时退回第一个启用仓库。"""
    warehouse = db.scalar(
        select(Warehouse).where(Warehouse.is_active == True).order_by(  # noqa: E712
            Warehouse.is_default.desc(), Warehouse.id
        )
    )
    if not warehouse:
        raise HTTPException(status_code=400, detail="系统尚未配置可用仓库，请先在仓库管理中新增仓库")
    return warehouse


def default_location(db: Session, warehouse: Warehouse) -> StorageLocation:
    location = db.scalar(
        select(StorageLocation)
        .where(StorageLocation.warehouse_id == warehouse.id)
        .where(StorageLocation.is_active == True)  # noqa: E712
        .order_by(StorageLocation.id)
    )
    if not location:
        raise HTTPException(
            status_code=400, detail=f"仓库「{warehouse.name}」尚未配置库位，请先新增库位"
        )
    return location


def resolve_target(
    db: Session, warehouse_id: int | None, location_id: int | None
) -> tuple[Warehouse, StorageLocation]:
    """校验并解析目标仓库/库位；未指定时落到默认仓库的第一个库位。"""
    if warehouse_id:
        warehouse = db.get(Warehouse, warehouse_id)
        if not warehouse:
            raise HTTPException(status_code=404, detail="仓库不存在")
        if not warehouse.is_active:
            raise HTTPException(status_code=400, detail=f"仓库「{warehouse.name}」已停用")
    else:
        warehouse = default_warehouse(db)

    if location_id:
        location = db.get(StorageLocation, location_id)
        if not location:
            raise HTTPException(status_code=404, detail="库位不存在")
        if location.warehouse_id != warehouse.id:
            raise HTTPException(status_code=400, detail="库位不属于所选仓库")
        if not location.is_active:
            raise HTTPException(status_code=400, detail=f"库位「{location.code}」已停用")
    else:
        location = default_location(db, warehouse)
    return warehouse, location


def _balance(
    db: Session, product_id: int, warehouse_id: int, location_id: int, batch_no: str = ""
) -> StockBalance | None:
    return db.scalar(
        select(StockBalance)
        .where(StockBalance.product_id == product_id)
        .where(StockBalance.warehouse_id == warehouse_id)
        .where(StockBalance.location_id == location_id)
        .where(StockBalance.batch_no == (batch_no or ""))
    )


def batch_available(
    db: Session, product_id: int, warehouse_id: int | None = None, location_id: int | None = None
) -> int:
    """某库位（或全仓）的现存量合计，跨批次汇总。"""
    stmt = select(func.coalesce(func.sum(StockBalance.quantity), 0)).where(
        StockBalance.product_id == product_id
    )
    if warehouse_id:
        stmt = stmt.where(StockBalance.warehouse_id == warehouse_id)
    if location_id:
        stmt = stmt.where(StockBalance.location_id == location_id)
    return int(db.scalar(stmt) or 0)


def sync_product_stock(db: Session, product: Product) -> int:
    """把商品的库位余额汇总回写到商品总库存，返回汇总值。"""
    db.flush()  # 先落库，保证汇总包含本次余额变更
    total = db.scalar(
        select(func.sum(StockBalance.quantity)).where(StockBalance.product_id == product.id)
    ) or 0
    product.stock_qty = int(total)
    return product.stock_qty


def apply_stock_change(
    db: Session,
    product: Product,
    change_type: str,
    quantity: int,
    *,
    warehouse_id: int | None = None,
    location_id: int | None = None,
    batch_no: str = "",
    expiry_date=None,
    reason: str = "",
    biz_type: str = "手工出入库",
    ref_no: str = "",
    username: str = "",
    unit_cost: float | None = None,
) -> dict:
    """在指定库位/批次上入/出库或盘点，写入流水并同步商品总库存。

    change_type: in=增加 quantity；out=减少 quantity；check=把该库位库存盘点为 quantity。
    盘点不带批次时按库位合计口径比较，差异落在默认批次（空批次）上。
    返回 change_qty / stock_before / stock_after。
    """
    if change_type not in CHANGE_TYPE_NAMES:
        raise HTTPException(status_code=400, detail="不支持的调整类型")
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="数量必须大于 0")

    warehouse, location = resolve_target(db, warehouse_id, location_id)
    batch = (batch_no or "").strip()[:60]

    if change_type == "check" and not batch:
        return _check_location_total(
            db,
            product,
            warehouse,
            location,
            quantity,
            reason=reason,
            biz_type=biz_type,
            ref_no=ref_no,
            username=username,
        )

    balance = _balance(db, product.id, warehouse.id, location.id, batch)

    if change_type == "check":
        before_total = batch_available(db, product.id, warehouse.id, location.id)
        delta = quantity - before_total
        before = before_total
        if delta == 0:
            raise HTTPException(status_code=400, detail="盘点数量与该库位当前库存一致，无需调整")
    else:
        before = balance.quantity if balance else 0
        delta = quantity if change_type == "in" else -quantity
        if delta < 0 and before + delta < 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"库存不足：{warehouse.name} / {location.code}"
                    f"{' 批次 ' + batch if batch else ''} 当前 {before}，需出库 {quantity}"
                ),
            )

    after = before + delta
    if balance is None:
        balance = StockBalance(
            product_id=product.id,
            warehouse_id=warehouse.id,
            location_id=location.id,
            batch_no=batch,
            expiry_date=expiry_date,
            quantity=0,
        )
        db.add(balance)
    # 已有批次补效期：批次号相同视为同一批，效期以最新入库为准
    if expiry_date is not None:
        balance.expiry_date = expiry_date
    balance.quantity = after

    db.add(
        StockLog(
            product_id=product.id,
            warehouse_id=warehouse.id,
            location_id=location.id,
            batch_no=batch,
            change_type=change_type,
            biz_type=biz_type,
            ref_no=ref_no or "",
            change_qty=delta,
            stock_before=before,
            stock_after=after,
            reason=reason,
            created_by=username,
        )
    )
    # 移动加权平均成本：仅入库且给出成本价时重算，出库沿用当前均价（加权口径）
    if change_type == "in" and unit_cost is not None and unit_cost > 0:
        total_before = int(product.stock_qty or 0)
        old_avg = float(product.avg_cost or 0.0)
        new_total = total_before + quantity
        if new_total > 0:
            product.avg_cost = round((total_before * old_avg + quantity * unit_cost) / new_total, 4)

    sync_product_stock(db, product)
    return {
        "product_id": product.id,
        "warehouse_id": warehouse.id,
        "warehouse_name": warehouse.name,
        "location_id": location.id,
        "location_code": location.code,
        "batch_no": batch,
        "stock_before": before,
        "stock_after": after,
        "change_qty": delta,
    }


def _check_location_total(
    db: Session,
    product: Product,
    warehouse: Warehouse,
    location: StorageLocation,
    quantity: int,
    *,
    reason: str = "",
    biz_type: str = "盘点调整",
    ref_no: str = "",
    username: str = "",
) -> dict:
    """盘点按「库位合计」口径调整：盘亏跨批次按先到期先出扣，盘盈记到空批次。"""
    before_total = batch_available(db, product.id, warehouse.id, location.id)
    delta = quantity - before_total
    if delta == 0:
        raise HTTPException(status_code=400, detail="盘点数量与该库位当前库存一致，无需调整")

    if delta > 0:
        balance = _balance(db, product.id, warehouse.id, location.id, "")
        if balance is None:
            db.add(
                StockBalance(
                    product_id=product.id,
                    warehouse_id=warehouse.id,
                    location_id=location.id,
                    batch_no="",
                    quantity=delta,
                )
            )
        else:
            balance.quantity = int(balance.quantity) + delta
        db.add(
            StockLog(
                product_id=product.id,
                warehouse_id=warehouse.id,
                location_id=location.id,
                batch_no="",
                change_type="check",
                biz_type=biz_type,
                ref_no=ref_no or "",
                change_qty=delta,
                stock_before=before_total,
                stock_after=quantity,
                reason=reason,
                created_by=username,
            )
        )
    else:
        need = -delta
        rows = db.scalars(
            select(StockBalance)
            .where(StockBalance.product_id == product.id)
            .where(StockBalance.warehouse_id == warehouse.id)
            .where(StockBalance.location_id == location.id)
            .where(StockBalance.quantity > 0)
            .order_by(
                StockBalance.batch_no == "",
                StockBalance.expiry_date.is_(None),
                StockBalance.expiry_date,
                StockBalance.id,
            )
        ).all()
        for row in rows:
            if need <= 0:
                break
            take = min(int(row.quantity), need)
            row_before = int(row.quantity)
            row.quantity = row_before - take
            need -= take
            db.add(
                StockLog(
                    product_id=product.id,
                    warehouse_id=warehouse.id,
                    location_id=location.id,
                    batch_no=row.batch_no,
                    change_type="check",
                    biz_type=biz_type,
                    ref_no=ref_no or "",
                    change_qty=-take,
                    stock_before=row_before,
                    stock_after=row.quantity,
                    reason=reason,
                    created_by=username,
                )
            )

    sync_product_stock(db, product)
    return {
        "product_id": product.id,
        "warehouse_id": warehouse.id,
        "warehouse_name": warehouse.name,
        "location_id": location.id,
        "location_code": location.code,
        "batch_no": "",
        "stock_before": before_total,
        "stock_after": quantity,
        "change_qty": delta,
    }


def deduct_stock(
    db: Session,
    product: Product,
    quantity: int,
    *,
    reason: str = "",
    biz_type: str = "手工出库",
    ref_no: str = "",
    username: str = "",
) -> list[dict]:
    """跨库位扣减：默认仓库优先，批次按先到期先出（FEFO），逐库位写流水。"""
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="出库数量必须大于 0")

    rows = db.execute(
        select(StockBalance, Warehouse.code, Warehouse.name, StorageLocation.code, StorageLocation.name)
        .join(Warehouse, Warehouse.id == StockBalance.warehouse_id)
        .join(StorageLocation, StorageLocation.id == StockBalance.location_id)
        .where(StockBalance.product_id == product.id)
        .where(StockBalance.quantity > 0)
        .where(Warehouse.is_active == True)  # noqa: E712
        .where(StorageLocation.is_active == True)  # noqa: E712
        .order_by(
            Warehouse.is_default.desc(),
            Warehouse.id,
            # 有批次的先出、效期近的先出；无批次且无效期的排最后
            StockBalance.batch_no == "",
            StockBalance.expiry_date.is_(None),
            StockBalance.expiry_date,
            StockBalance.quantity.desc(),
            StockBalance.id,
        )
    ).all()

    available = sum(row[0].quantity for row in rows)
    if available < quantity:
        raise HTTPException(
            status_code=400,
            detail=f"库存不足：{product.name} 可用库存 {available}，需要 {quantity}",
        )

    results: list[dict] = []
    remaining = quantity
    for balance, wh_code, wh_name, loc_code, loc_name in rows:
        if remaining <= 0:
            break
        take = min(balance.quantity, remaining)
        before = balance.quantity
        balance.quantity = before - take
        remaining -= take
        db.add(
            StockLog(
                product_id=product.id,
                warehouse_id=balance.warehouse_id,
                location_id=balance.location_id,
                batch_no=balance.batch_no,
                change_type="out",
                biz_type=biz_type,
                ref_no=ref_no or "",
                change_qty=-take,
                stock_before=before,
                stock_after=balance.quantity,
                reason=reason,
                created_by=username,
            )
        )
        results.append(
            {
                "warehouse_id": balance.warehouse_id,
                "warehouse_name": wh_name,
                "location_id": balance.location_id,
                "location_code": loc_code,
                "location_name": loc_name,
                "batch_no": balance.batch_no,
                "change_qty": -take,
                "stock_before": before,
                "stock_after": balance.quantity,
            }
        )
    sync_product_stock(db, product)
    return results
