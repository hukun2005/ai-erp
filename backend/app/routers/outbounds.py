"""销售出库单（发货）接口。

出库单是销售订单与库存之间的交付凭证：确认出库才扣库存，
支持分批发货、物流单号与签收登记。
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..audit import record
from ..database import get_db
from ..deps import require_perm
from ..models import Customer, OutboundOrder, SalesOrder, User
from ..schemas import OutboundAction, OutboundCreate, OutboundUpdate
from ..scoping import ensure_owner
from ..services import approval_flow, approvals
from ..services import outbound as outbound_service

router = APIRouter(prefix="/api/outbounds", tags=["销售出库"])

STATUS_TEXT = outbound_service.STATUS_TEXT


def _load(db: Session, outbound_id: int) -> OutboundOrder:
    outbound = db.scalar(
        select(OutboundOrder)
        .options(
            selectinload(OutboundOrder.items),
            selectinload(OutboundOrder.customer),
            selectinload(OutboundOrder.warehouse),
            selectinload(OutboundOrder.sales_order),
        )
        .where(OutboundOrder.id == outbound_id)
    )
    if not outbound:
        raise HTTPException(status_code=404, detail="出库单不存在")
    return outbound


@router.get("", summary="出库单列表")
def list_outbounds(
    keyword: str = Query(""),
    status: str = Query(""),
    customer_id: int | None = Query(None),
    start_date: str = Query("", description="创建日期起（YYYY-MM-DD，含当天）"),
    end_date: str = Query("", description="创建日期止（YYYY-MM-DD，含当天）"),
    sort: str = Query("", description="created_desc / ship_desc / amount_desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("outbound:view")),
):
    from .. import list_filters

    stmt = select(OutboundOrder).join(Customer, Customer.id == OutboundOrder.customer_id)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            or_(
                OutboundOrder.outbound_no.like(like),
                OutboundOrder.logistics_no.like(like),
                Customer.name.like(like),
            )
        )
    if status:
        stmt = stmt.where(OutboundOrder.status == status)
    if customer_id:
        stmt = stmt.where(OutboundOrder.customer_id == customer_id)
    start = list_filters.parse_day(start_date, "开始日期")
    end = list_filters.parse_day(end_date, "结束日期")
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    if start:
        stmt = stmt.where(OutboundOrder.created_at >= start)
    if end:
        stmt = stmt.where(OutboundOrder.created_at < end + timedelta(days=1))

    subquery = stmt.subquery()
    total = db.scalar(select(func.count()).select_from(subquery)) or 0
    amount_sum = db.scalar(
        select(func.coalesce(func.sum(subquery.c.total_amount), 0.0)).select_from(subquery)
    ) or 0.0

    sort_key = list_filters.resolve_sort(sort) if sort else "created_desc"
    order_map = {
        "created_desc": OutboundOrder.id.desc(),
        "created_asc": OutboundOrder.id.asc(),
        "date_desc": OutboundOrder.ship_date.desc(),
        "date_asc": OutboundOrder.ship_date.asc(),
        "amount_desc": OutboundOrder.total_amount.desc(),
        "amount_asc": OutboundOrder.total_amount.asc(),
    }
    rows = db.scalars(
        stmt.order_by(order_map[sort_key], OutboundOrder.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "total": total,
        "amount_sum": round(float(amount_sum), 2),
        "items": [outbound_service.serialize(row, with_items=False) for row in rows],
    }


@router.post("", summary="按销售订单生成出库单", dependencies=[Depends(require_perm("outbound:manage"))])
def create_outbound(
    payload: OutboundCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("outbound:manage")),
):
    order = db.get(SalesOrder, payload.sales_order_id)
    if not order:
        raise HTTPException(status_code=404, detail="销售订单不存在")
    ensure_owner(order.created_by, user, "发货")
    outbound = outbound_service.create_outbound(
        db,
        order,
        user=user,
        items=[item.model_dump() for item in payload.items],
        warehouse_id=payload.warehouse_id,
        logistics_company=payload.logistics_company,
        logistics_no=payload.logistics_no,
        remark=payload.remark,
    )
    db.commit()
    db.refresh(outbound)
    record(
        username=user.username,
        role=user.role,
        action="创建出库单",
        resource=f"出库单 {outbound.outbound_no}",
        detail=f"销售订单 {order.order_no}，数量 {outbound.total_quantity}，金额 {outbound.total_amount:,.2f}",
        request=request,
    )
    return outbound_service.serialize(outbound)


@router.get("/progress/{order_id}", summary="销售订单发货进度")
def order_progress(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("outbound:view")),
):
    order = db.get(SalesOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="销售订单不存在")
    return outbound_service.order_progress(db, order)


@router.get("/{outbound_id}", summary="出库单详情")
def get_outbound(
    outbound_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("outbound:view")),
):
    return outbound_service.serialize(_load(db, outbound_id))


@router.put("/{outbound_id}", summary="修改待发货出库单")
def update_outbound(
    outbound_id: int,
    payload: OutboundUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("outbound:manage")),
):
    outbound = _load(db, outbound_id)
    if outbound.status != "draft":
        raise HTTPException(status_code=400, detail="只有待发货状态可以修改")
    data = payload.model_dump(exclude_unset=True)
    for field in ("logistics_company", "logistics_no", "remark"):
        if field in data and data[field] is not None:
            setattr(outbound, field, data[field])
    if data.get("ship_date"):
        outbound.ship_date = data["ship_date"]
    db.commit()
    db.refresh(outbound)
    record(
        username=user.username,
        role=user.role,
        action="修改出库单",
        resource=f"出库单 {outbound.outbound_no}",
        detail="更新物流/备注信息",
        request=request,
    )
    return outbound_service.serialize(outbound)


@router.post("/{outbound_id}/status", summary="出库单状态变更（发货/签收/取消）")
def change_status(
    outbound_id: int,
    payload: OutboundAction,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("outbound:manage")),
):
    outbound = _load(db, outbound_id)
    action = (payload.action or "").strip()
    if action == "submit":
        approvals.ensure_status(outbound, ("draft",), "提交审批")
        outbound.approval_required = approval_flow.required_levels(
            db, "outbound", outbound.total_amount, approval_flow.context_for(db, "outbound", outbound)
        )
        outbound.approval_approved = 0
        outbound.status = "pending"
        approvals.log_action(db, "outbound", outbound.id, "submit", user, payload.comment)
    elif action == "approve":
        approvals.ensure_status(outbound, ("pending",), "审批通过")
        result = approval_flow.perform_approval(db, "outbound", outbound, user, payload.comment)
        if result["completed"]:
            # 审批全部通过才真正出库（ship 只接受 draft，先回退状态）
            outbound.status = "draft"
            outbound_service.ship(db, outbound, user)
        else:
            db.commit()
            db.refresh(outbound)
            data = outbound_service.serialize(outbound)
            data["pending_next_level"] = result["level_complete"]
            return data
    elif action == "reject":
        approvals.ensure_status(outbound, ("pending",), "审批驳回")
        level, _rule = approval_flow.ensure_level_permission(db, "outbound", outbound, user)
        outbound.status = "draft"
        outbound.approval_approved = 0
        approvals.log_action(
            db, "outbound", outbound.id, "reject", user, payload.comment or "驳回", level=level
        )
    elif action == "ship":
        # 配了出库审批规则就走审批，不允许绕过
        if approval_flow.has_rules(db, "outbound"):
            raise HTTPException(
                status_code=400,
                detail="已配置出库审批规则，请先「提交审批」，审批通过后系统自动出库",
            )
        outbound_service.ship(db, outbound, user)
    elif action == "sign":
        outbound_service.sign(db, outbound, payload.signed_by)
    elif action == "cancel":
        outbound_service.cancel(db, outbound, user)
    else:
        raise HTTPException(status_code=400, detail=f"不支持的操作：{action}")

    db.commit()
    db.refresh(outbound)
    label = {
        "submit": "提交审批",
        "approve": "审批通过",
        "reject": "审批驳回",
        "ship": "确认出库",
        "sign": "登记签收",
        "cancel": "取消出库",
    }[action]
    record(
        username=user.username,
        role=user.role,
        action=f"出库单{label}",
        resource=f"出库单 {outbound.outbound_no}",
        detail=(
            f"状态={STATUS_TEXT.get(outbound.status, outbound.status)}"
            + (f"：{payload.comment}" if payload.comment else "")
        ),
        request=request,
    )
    data = outbound_service.serialize(outbound)
    data["sales_progress"] = outbound_service.order_progress(db, outbound.sales_order)
    return data


@router.post("/{outbound_id}/scan", summary="扫码累加待发货数量")
def scan_increment(
    outbound_id: int,
    request: Request,
    code: str = Query(..., min_length=1, description="商品条码，兼容直接输入 SKU"),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("outbound:manage")),
):
    """扫码出货：扫到的商品在待发货明细里数量 +1，不能超过订单剩余可发量。"""
    from ..models import Product

    outbound = _load(db, outbound_id)
    if outbound.status != "draft":
        raise HTTPException(status_code=400, detail="只有待发货的出库单可以扫码累加数量")
    text = code.strip()
    product = db.scalar(select(Product).where(Product.barcode == text, Product.barcode != ""))
    if product is None:
        product = db.scalar(select(Product).where(Product.sku == text))
    if product is None:
        raise HTTPException(status_code=404, detail=f"未找到条码或 SKU 为「{text}」的商品")
    item = next((row for row in outbound.items if row.product_id == product.id), None)
    if item is None:
        raise HTTPException(
            status_code=400,
            detail=f"「{product.name}」不在本张出库单里，请先在销售订单里生成出库单",
        )
    if item.order_item_id:
        remaining = outbound_service.remaining_for_order(db, outbound.sales_order)
        if remaining.get(item.order_item_id, 0) < 1:
            raise HTTPException(
                status_code=400,
                detail=f"「{product.name}」已达到订单可发数量上限",
            )
    item.quantity = int(item.quantity) + 1
    item.amount = round(item.quantity * float(item.unit_price or 0), 2)
    outbound.total_quantity = sum(int(row.quantity) for row in outbound.items)
    outbound.total_amount = round(sum(float(row.amount) for row in outbound.items), 2)
    db.commit()
    db.refresh(outbound)
    record(
        username=user.username,
        role=user.role,
        action="扫码出库",
        resource=f"出库单 {outbound.outbound_no}",
        detail=f"{product.name} × {item.quantity}",
        request=request,
    )
    data = outbound_service.serialize(outbound)
    data["scanned_item_id"] = item.id
    return data


@router.delete("/{outbound_id}", summary="删除待发货出库单", dependencies=[Depends(require_perm("outbound:manage"))])
def delete_outbound(
    outbound_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("outbound:manage")),
):
    outbound = _load(db, outbound_id)
    if outbound.status != "draft":
        raise HTTPException(status_code=400, detail="只有待发货状态可以删除，已出库请先取消")
    outbound_no = outbound.outbound_no
    db.delete(outbound)
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="删除出库单",
        resource=f"出库单 {outbound_no}",
        detail="删除待发货出库单",
        request=request,
    )
    return {"ok": True}
