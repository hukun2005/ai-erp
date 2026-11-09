from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from ..audit import record
from ..config import settings
from ..database import get_db
from ..deps import require_perm
from ..models import (
    Product,
    StockBalance,
    StocktakeItem,
    StocktakeOrder,
    StorageLocation,
    User,
    Warehouse,
)
from ..schemas import StocktakeAction, StocktakeCreate, StocktakeSave
from ..services import approval_flow, approval_sla, approvals, stocktake_adjustment
from ..services.stock import apply_stock_change
from ..utils import gen_order_no

router = APIRouter(prefix="/api/stocktakes", tags=["库存盘点"])


def _serialize(item: StocktakeItem) -> dict:
    return {
        "id": item.id,
        "product_id": item.product_id,
        "product_name": item.product.name if item.product else "",
        "product_sku": item.product.sku if item.product else "",
        "location_id": item.location_id,
        "location_code": item.location.code if item.location else "",
        "book_qty": item.book_qty,
        "actual_qty": item.actual_qty,
        "diff_qty": item.diff_qty,
    }


def serialize_stocktake(
    stocktake: StocktakeOrder,
    with_items: bool = False,
    diff_count: int | None = None,
    diff_quantity: int | None = None,
) -> dict:
    if diff_count is None or diff_quantity is None:
        diffs = [item for item in stocktake.items if item.diff_qty]
        diff_count = len(diffs)
        diff_quantity = sum(abs(item.diff_qty) for item in diffs)
    return {
        "id": stocktake.id,
        "stocktake_no": stocktake.stocktake_no,
        "warehouse_id": stocktake.warehouse_id,
        "warehouse_name": stocktake.warehouse.name if stocktake.warehouse else "",
        "status": stocktake.status,
        "status_text": {
            "draft": "草稿",
            "pending": "待审批",
            "completed": "已完成",
            "cancelled": "已取消",
        }.get(stocktake.status, stocktake.status),
        "remark": stocktake.remark,
        "created_by": stocktake.created_by,
        "created_at": stocktake.created_at,
        "completed_at": stocktake.completed_at,
        "submitted_by": stocktake.submitted_by,
        "submitted_at": stocktake.submitted_at,
        "approved_by": stocktake.approved_by,
        "approved_at": stocktake.approved_at,
        "approve_comment": stocktake.approve_comment,
        "approval_required": int(stocktake.approval_required or 0),
        "approval_approved": int(stocktake.approval_approved or 0),
        "needs_approval": needs_approval(),
        "diff_count": diff_count,
        "diff_quantity": diff_quantity,
        "items": [_serialize(item) for item in stocktake.items] if with_items else [],
    }


def needs_approval() -> bool:
    """盘点差异是否必须二次审批后才调整库存。"""
    return settings.STOCKTAKE_APPROVAL not in ("0", "false", "False", "")


def _generate_adjustments(db: Session, stocktake: StocktakeOrder, user: User) -> list:
    """盘点完成后生成追溯用调整单；失败不影响盘点本身。"""
    try:
        return stocktake_adjustment.generate(db, stocktake, user.username)
    except ValueError:
        return []


def _with_adjustments(data: dict, docs: list) -> dict:
    data["adjustments"] = [stocktake_adjustment.serialize(doc) for doc in docs]
    return data


def _apply_diffs(db: Session, stocktake: StocktakeOrder, user) -> int:
    """把实盘差异写成库存调整流水，返回调整的明细行数。"""
    adjusted = 0
    for item in stocktake.items:
        if item.actual_qty is None:
            item.actual_qty = item.book_qty
            item.diff_qty = 0
        if item.diff_qty == 0:
            continue
        product = db.get(Product, item.product_id)
        if not product:
            continue
        apply_stock_change(
            db,
            product,
            "check",
            item.actual_qty,
            warehouse_id=stocktake.warehouse_id,
            location_id=item.location_id,
            reason=f"库存盘点 {stocktake.stocktake_no}：账面 {item.book_qty}，实盘 {item.actual_qty}",
            biz_type="盘点调整",
            ref_no=stocktake.stocktake_no,
            username=user.username,
        )
        adjusted += 1
    return adjusted


def _get(db: Session, stocktake_id: int) -> StocktakeOrder:
    stocktake = db.get(StocktakeOrder, stocktake_id)
    if not stocktake:
        raise HTTPException(status_code=404, detail="盘点单不存在")
    return stocktake


@router.get("", summary="盘点单列表")
def list_stocktakes(
    warehouse_id: int | None = Query(None),
    status: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("stocktake:view")),
):
    stmt = select(StocktakeOrder)
    if warehouse_id:
        stmt = stmt.where(StocktakeOrder.warehouse_id == warehouse_id)
    if status:
        stmt = stmt.where(StocktakeOrder.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(StocktakeOrder.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    # 差异统计用一次聚合查询，避免每行懒加载全部明细（大单据场景）
    diff_map: dict[int, tuple[int, int]] = {}
    if rows:
        diff_rows = db.execute(
            select(
                StocktakeItem.stocktake_id,
                func.count(),
                func.coalesce(func.sum(func.abs(StocktakeItem.diff_qty)), 0),
            )
            .where(StocktakeItem.stocktake_id.in_([row.id for row in rows]))
            .where(StocktakeItem.diff_qty != 0)
            .group_by(StocktakeItem.stocktake_id)
        ).all()
        diff_map = {rid: (int(cnt), int(qty)) for rid, cnt, qty in diff_rows}
    return {
        "total": total,
        "items": [
            serialize_stocktake(row, diff_count=diff_map.get(row.id, (0, 0))[0], diff_quantity=diff_map.get(row.id, (0, 0))[1])
            for row in rows
        ],
    }


@router.get("/{stocktake_id}", summary="盘点单详情")
def get_stocktake(
    stocktake_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    all_items: bool = Query(False),
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("stocktake:view")),
):
    stocktake = _get(db, stocktake_id)
    docs = stocktake_adjustment.existing(db, stocktake.stocktake_no)

    # 明细统计用聚合查询，避免把整张盘点单的 items 一次性加载（大单据场景）
    item_total = db.scalar(
        select(func.count()).select_from(StocktakeItem).where(StocktakeItem.stocktake_id == stocktake_id)
    ) or 0
    diff = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(func.abs(StocktakeItem.diff_qty)), 0),
        )
        .where(StocktakeItem.stocktake_id == stocktake_id)
        .where(StocktakeItem.diff_qty != 0)
    ).one()
    diff_count = int(diff[0])
    diff_quantity = int(diff[1])

    item_stmt = select(StocktakeItem).options(
        joinedload(StocktakeItem.product), joinedload(StocktakeItem.location)
    ).where(StocktakeItem.stocktake_id == stocktake_id).order_by(StocktakeItem.id)
    if not all_items:
        item_stmt = item_stmt.offset((page - 1) * page_size).limit(page_size)
    item_rows = db.scalars(item_stmt).all()

    data = serialize_stocktake(stocktake, with_items=False, diff_count=diff_count, diff_quantity=diff_quantity)
    data["items"] = [_serialize(item) for item in item_rows]
    data["item_total"] = item_total
    data["page"] = page
    data["page_size"] = page_size
    return _with_adjustments(data, docs)


@router.post("", summary="创建盘点单（冻结账面数量）")
def create_stocktake(
    payload: StocktakeCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("stocktake:manage")),
):
    warehouse = db.get(Warehouse, payload.warehouse_id)
    if not warehouse:
        raise HTTPException(status_code=404, detail="仓库不存在")
    if not warehouse.is_active:
        raise HTTPException(status_code=400, detail=f"仓库「{warehouse.name}」已停用")

    balances = db.execute(
        select(StockBalance, StorageLocation)
        .join(StorageLocation, StorageLocation.id == StockBalance.location_id)
        .where(StockBalance.warehouse_id == warehouse.id)
        .where(StockBalance.quantity > 0)
        .order_by(StockBalance.location_id, StockBalance.product_id)
    ).all()
    if not balances:
        raise HTTPException(status_code=400, detail="该仓库暂无库存，无需盘点")

    stocktake = StocktakeOrder(
        stocktake_no=gen_order_no(db, StocktakeOrder, "ST", field="stocktake_no"),
        warehouse_id=warehouse.id,
        status="draft",
        remark=payload.remark or "",
        created_by=user.username,
    )
    for balance, location in balances:
        stocktake.items.append(
            StocktakeItem(
                product_id=balance.product_id,
                location_id=location.id,
                book_qty=int(balance.quantity),
                actual_qty=None,
                diff_qty=0,
            )
        )
    db.add(stocktake)
    db.commit()
    db.refresh(stocktake)
    record(
        username=user.username,
        role=user.role,
        action="创建盘点单",
        resource=f"盘点单 {stocktake.stocktake_no}",
        detail=f"仓库 {warehouse.name}，共 {len(stocktake.items)} 个库位商品",
        request=request,
    )
    return serialize_stocktake(stocktake, with_items=True)


@router.post("/{stocktake_id}/items", summary="录入实盘数量")
def save_items(
    stocktake_id: int,
    payload: StocktakeSave,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("stocktake:manage")),
):
    stocktake = _get(db, stocktake_id)
    if stocktake.status != "draft":
        raise HTTPException(status_code=400, detail="只有草稿盘点单可以录入实盘")
    item_map = {item.id: item for item in stocktake.items}
    for row in payload.items:
        item = item_map.get(row.item_id)
        if not item:
            raise HTTPException(status_code=400, detail=f"盘点明细 {row.item_id} 不存在")
        item.actual_qty = row.actual_qty
        item.diff_qty = row.actual_qty - item.book_qty
    db.commit()
    db.refresh(stocktake)
    return serialize_stocktake(stocktake, with_items=True)


@router.post("/{stocktake_id}/status", summary="提交/审批/取消盘点")
def change_status(
    stocktake_id: int,
    payload: StocktakeAction,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("stocktake:manage")),
):
    stocktake = _get(db, stocktake_id)
    action = payload.action

    if action == "cancel":
        if stocktake.status not in ("draft", "pending"):
            raise HTTPException(status_code=400, detail="只有草稿或待审批盘点单可以取消")
        stocktake.status = "cancelled"
        stocktake.cancelled_at = datetime.now()
        db.commit()
        db.refresh(stocktake)
        record(
            username=user.username,
            role=user.role,
            action="取消盘点单",
            resource=f"盘点单 {stocktake.stocktake_no}",
            detail="取消盘点单，未调整库存",
            request=request,
        )
        return serialize_stocktake(stocktake, with_items=True)

    if action == "complete":
        if stocktake.status != "draft":
            raise HTTPException(status_code=400, detail="只有草稿盘点单可以提交")
        # 未录入的明细视为账实一致，不产生调整
        for item in stocktake.items:
            if item.actual_qty is None:
                item.actual_qty = item.book_qty
                item.diff_qty = 0
        has_diff = any(item.diff_qty for item in stocktake.items)
        if has_diff and approval_flow.has_rules(db, "stocktake"):
            # 配置了盘点审批规则：走通用多级审批（会签/或签/委托）
            stocktake.status = "pending"
            stocktake.approval_required = approval_flow.required_levels(
                db, "stocktake", 0, approval_flow.context_for(db, "stocktake", stocktake)
            )
            stocktake.approval_approved = 0
            stocktake.submitted_by = user.username
            stocktake.submitted_at = datetime.now()
            approvals.log_action(db, "stocktake", stocktake.id, "submit", user)
            db.commit()
            db.refresh(stocktake)
            record(
                username=user.username,
                role=user.role,
                action="提交盘点差异审批",
                resource=f"盘点单 {stocktake.stocktake_no}",
                detail=f"差异 {len([i for i in stocktake.items if i.diff_qty])} 项，按盘点审批规则逐级审批",
                request=request,
            )
            return serialize_stocktake(stocktake, with_items=True)

        if needs_approval() and has_diff:
            # 有差异时先挂起，等第二个人复核，避免自己盘自己改账
            stocktake.status = "pending"
            stocktake.submitted_by = user.username
            stocktake.submitted_at = datetime.now()
            db.commit()
            db.refresh(stocktake)
            record(
                username=user.username,
                role=user.role,
                action="提交盘点差异审批",
                resource=f"盘点单 {stocktake.stocktake_no}",
                detail=f"差异 {len([i for i in stocktake.items if i.diff_qty])} 项，等待复核",
                request=request,
            )
            return serialize_stocktake(stocktake, with_items=True)

        adjusted = _apply_diffs(db, stocktake, user)
        stocktake.status = "completed"
        stocktake.completed_at = datetime.now()
        if payload.remark:
            stocktake.remark = f"{stocktake.remark}｜{payload.remark}".strip("｜")
        db.commit()
        db.refresh(stocktake)
        docs = _generate_adjustments(db, stocktake, user)
        record(
            username=user.username,
            role=user.role,
            action="完成盘点",
            resource=f"盘点单 {stocktake.stocktake_no}",
            detail=f"调整 {adjusted} 个库位商品，生成调整单 {len(docs)} 张",
            request=request,
        )
        return _with_adjustments(serialize_stocktake(stocktake, with_items=True), docs)

    if action == "approve":
        if stocktake.status != "pending":
            raise HTTPException(status_code=400, detail="只有待审批盘点单可以通过")
        if approval_flow.has_rules(db, "stocktake"):
            result = approval_flow.perform_approval(
                db, "stocktake", stocktake, user, payload.remark or ""
            )
            if not result["completed"]:
                db.commit()
                db.refresh(stocktake)
                data = serialize_stocktake(stocktake, with_items=True)
                data["pending_next_level"] = result["level_complete"]
                return data
        elif stocktake.submitted_by and stocktake.submitted_by == user.username and user.role != "admin":
            raise HTTPException(status_code=400, detail="盘点差异需要由其他人复核")
        adjusted = _apply_diffs(db, stocktake, user)
        stocktake.status = "completed"
        stocktake.completed_at = datetime.now()
        stocktake.approved_by = user.username
        stocktake.approved_at = datetime.now()
        stocktake.approve_comment = (payload.remark or "")[:300]
        db.commit()
        db.refresh(stocktake)
        docs = _generate_adjustments(db, stocktake, user)
        record(
            username=user.username,
            role=user.role,
            action="审批盘点差异",
            resource=f"盘点单 {stocktake.stocktake_no}",
            detail=f"通过并调整 {adjusted} 个库位商品，生成调整单 {len(docs)} 张",
            request=request,
        )
        return _with_adjustments(serialize_stocktake(stocktake, with_items=True), docs)

    if action == "reject":
        if stocktake.status != "pending":
            raise HTTPException(status_code=400, detail="只有待审批盘点单可以驳回")
        stocktake.status = "draft"
        stocktake.submitted_by = ""
        stocktake.submitted_at = None
        stocktake.approval_approved = 0
        stocktake.approve_comment = (payload.remark or "驳回")[:300]
        approvals.log_action(db, "stocktake", stocktake.id, "reject", user, payload.remark or "驳回")
        db.commit()
        db.refresh(stocktake)
        record(
            username=user.username,
            role=user.role,
            action="驳回盘点差异",
            resource=f"盘点单 {stocktake.stocktake_no}",
            detail=payload.remark or "驳回",
            request=request,
        )
        return serialize_stocktake(stocktake, with_items=True)

    raise HTTPException(status_code=400, detail=f"不支持的操作：{action}")


@router.post("/{stocktake_id}/adjustment", summary="按盘点差异生成库存调整单")
def generate_adjustment(
    stocktake_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("stocktake:manage")),
):
    """生成追溯用调整单（已过账）；重复调用返回之前生成的单据，不会重复建单。"""
    stocktake = _get(db, stocktake_id)
    try:
        docs = stocktake_adjustment.generate(db, stocktake, user.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record(
        username=user.username,
        role=user.role,
        action="生成盘点调整单",
        resource=f"盘点单 {stocktake.stocktake_no}",
        detail=f"生成 {len(docs)} 张调整单：" + "、".join(doc.adjust_no for doc in docs),
        request=request,
    )
    return {"adjustments": [stocktake_adjustment.serialize(doc) for doc in docs]}


@router.delete("/{stocktake_id}", summary="删除草稿盘点单")
def delete_stocktake(
    stocktake_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("stocktake:manage")),
):
    stocktake = _get(db, stocktake_id)
    if stocktake.status != "draft":
        raise HTTPException(status_code=400, detail="仅草稿盘点单可删除")
    stocktake_no = stocktake.stocktake_no
    db.delete(stocktake)
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="删除盘点单",
        resource=f"盘点单 {stocktake_no}",
        detail="删除草稿盘点单",
        request=request,
    )
    return {"ok": True}
