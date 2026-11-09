from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import require_perm
from ..models import Product, PurchaseOrder, PurchaseOrderItem, PurchaseRequisition, PurchaseRequisitionItem, User
from ..schemas import RequisitionAction, RequisitionConvert, RequisitionCreate, RequisitionUpdate
from ..services import approval_flow, approvals
from ..utils import gen_order_no

router = APIRouter(prefix="/api/purchase-requisitions", tags=["采购申请"])


def serialize_req(req: PurchaseRequisition) -> dict:
    return {
        "id": req.id,
        "req_no": req.req_no,
        "status": req.status,
        "needed_date": req.needed_date,
        "remark": req.remark,
        "approved_by": req.approved_by,
        "approved_at": req.approved_at,
        "converted_order_id": req.converted_order_id,
        "created_by": req.created_by,
        "created_at": req.created_at,
        "total_amount": round(sum(item.amount for item in req.items), 2),
        "approval_required": int(req.approval_required or 0),
        "approval_approved": int(req.approval_approved or 0),
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product.name if item.product else "",
                "product_sku": item.product.sku if item.product else "",
                "unit": item.product.unit if item.product else "",
                "quantity": item.quantity,
                "est_price": item.est_price,
                "amount": item.amount,
                "remark": item.remark,
            }
            for item in req.items
        ],
    }


def _get(db: Session, req_id: int) -> PurchaseRequisition:
    req = db.get(PurchaseRequisition, req_id)
    if not req:
        raise HTTPException(status_code=404, detail="采购申请不存在")
    return req


def _apply_items(db: Session, req: PurchaseRequisition, items) -> None:
    req.items.clear()
    for entry in items:
        product_id = entry.get("product_id") if isinstance(entry, dict) else entry.product_id
        quantity = entry.get("quantity") if isinstance(entry, dict) else entry.quantity
        est_price = entry.get("est_price") if isinstance(entry, dict) else entry.est_price
        remark = entry.get("remark", "") if isinstance(entry, dict) else entry.remark
        product = db.get(Product, product_id)
        if not product:
            raise HTTPException(status_code=400, detail=f"商品 {product_id} 不存在")
        req.items.append(
            PurchaseRequisitionItem(
                product_id=product_id,
                quantity=quantity,
                est_price=est_price,
                amount=round(quantity * est_price, 2),
                remark=remark,
            )
        )


@router.get("", summary="采购申请列表")
def list_requisitions(
    keyword: str = Query(""),
    status: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("requisition:view")),
):
    stmt = select(PurchaseRequisition)
    if keyword:
        stmt = stmt.where(PurchaseRequisition.req_no.like(f"%{keyword}%"))
    if status:
        stmt = stmt.where(PurchaseRequisition.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(PurchaseRequisition.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"total": total, "items": [serialize_req(row) for row in rows]}


@router.get("/{req_id}", summary="采购申请详情")
def get_requisition(
    req_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("requisition:view")),
):
    return serialize_req(_get(db, req_id))


@router.post("", summary="新建采购申请", dependencies=[Depends(require_perm("requisition:manage"))])
def create_requisition(
    payload: RequisitionCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("requisition:manage")),
):
    req = PurchaseRequisition(
        req_no=gen_order_no(db, PurchaseRequisition, "PR", field="req_no"),
        needed_date=payload.needed_date,
        remark=payload.remark,
        created_by=user.username,
        status="draft",
    )
    _apply_items(db, req, payload.items)
    db.add(req)
    db.commit()
    db.refresh(req)
    record(
        username=user.username,
        role=user.role,
        action="新建采购申请",
        resource=f"采购申请 {req.req_no}",
        detail=f"明细 {len(req.items)} 行",
        request=request,
    )
    return serialize_req(req)


@router.put("/{req_id}", summary="编辑草稿采购申请")
def update_requisition(
    req_id: int,
    payload: RequisitionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("requisition:manage")),
):
    req = _get(db, req_id)
    if req.status != "draft":
        raise HTTPException(status_code=400, detail="只有草稿采购申请可以编辑")
    data = payload.model_dump(exclude_unset=True)
    if "needed_date" in data:
        req.needed_date = data["needed_date"]
    if "remark" in data:
        req.remark = data["remark"] or ""
    if "items" in data:
        if not data["items"]:
            raise HTTPException(status_code=400, detail="申请明细不能为空")
        _apply_items(db, req, data["items"])
    db.commit()
    db.refresh(req)
    return serialize_req(req)


@router.post("/{req_id}/status", summary="提交 / 审批 / 驳回 / 取消采购申请")
def change_status(
    req_id: int,
    payload: RequisitionAction,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("requisition:view")),
):
    req = _get(db, req_id)
    action = payload.action
    req.total_amount = _total(req)  # approval_flow 按金额匹配审批规则
    if action == "submit":
        if req.status != "draft":
            raise HTTPException(status_code=400, detail="只有草稿申请可以提交")
        req.approval_required = approval_flow.required_levels(
            db, "requisition", req.total_amount, approval_flow.context_for(db, "requisition", req)
        )
        req.approval_approved = 0
        req.status = "pending"
        approvals.log_action(db, "requisition", req.id, "submit", user, payload.comment)
    elif action == "approve":
        if req.status != "pending":
            raise HTTPException(status_code=400, detail="只有待审批申请可以通过")
        result = approval_flow.perform_approval(db, "requisition", req, user, payload.comment)
        if result["completed"]:
            req.status = "approved"
            req.approved_by = user.username
            req.approved_at = datetime.now()
        else:
            # 会签未齐或还有下一级：保持待审批，返回审批链
            db.commit()
            db.refresh(req)
            data = serialize_req(req)
            data["approval"] = approval_flow.describe(
                db, "requisition", _total(req), req.approval_approved, order_id=req.id
            )
            data["pending_next_level"] = result["level_complete"]
            return data
    elif action == "reject":
        if req.status != "pending":
            raise HTTPException(status_code=400, detail="只有待审批申请可以驳回")
        level, _rule = approval_flow.ensure_level_permission(db, "requisition", req, user)
        req.status = "rejected"
        req.approval_approved = 0
        approvals.log_action(
            db, "requisition", req.id, "reject", user, payload.comment or "驳回", level=level
        )
    elif action == "cancel":
        if req.status in ("converted", "cancelled"):
            raise HTTPException(status_code=400, detail="该申请已转采购单或已取消")
        req.status = "cancelled"
    else:
        raise HTTPException(status_code=400, detail=f"不支持的操作：{action}")
    db.commit()
    db.refresh(req)
    data = serialize_req(req)
    data["approval"] = approval_flow.describe(
        db, "requisition", _total(req), req.approval_approved, order_id=req.id
    )
    return data


def _total(req: PurchaseRequisition) -> float:
    return round(sum(float(item.amount or 0) for item in req.items), 2)


def _can_approve(user: User) -> bool:
    from ..permissions import has_permission

    return has_permission(user.role, "purchase:approve") or has_permission(user.role, "requisition:manage")


@router.post("/{req_id}/convert", summary="采购申请转采购订单（草稿）")
def convert_to_order(
    req_id: int,
    payload: RequisitionConvert,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("requisition:manage")),
):
    req = _get(db, req_id)
    if req.status not in ("approved", "pending"):
        raise HTTPException(status_code=400, detail="只有已批准（或待审批）的申请可以转采购订单")
    if req.status == "pending" and not _can_approve(user):
        raise HTTPException(status_code=403, detail="待审批申请需审批通过后才能转采购订单")
    if req.converted_order_id:
        raise HTTPException(status_code=400, detail="该申请已转成采购订单")

    from ..models import Supplier

    supplier = db.get(Supplier, payload.supplier_id)
    if not supplier:
        raise HTTPException(status_code=400, detail="供应商不存在")

    order = PurchaseOrder(
        order_no=gen_order_no(db, PurchaseOrder, "PO"),
        supplier_id=supplier.id,
        order_date=datetime.now(),
        remark=f"由采购申请 {req.req_no} 转入",
        created_by=user.username,
        status="draft",
    )
    total = 0.0
    for item in req.items:
        amount = round(item.quantity * item.est_price, 2)
        total += amount
        order.items.append(
            PurchaseOrderItem(
                product_id=item.product_id,
                quantity=item.quantity,
                unit_price=item.est_price,
                amount=amount,
            )
        )
    order.total_amount = round(total, 2)
    db.add(order)
    db.flush()
    req.status = "converted"
    req.converted_order_id = order.id
    db.commit()
    db.refresh(req)
    record(
        username=user.username,
        role=user.role,
        action="采购申请转采购订单",
        resource=f"采购申请 {req.req_no}",
        detail=f"供应商 {supplier.name}，生成采购订单 {order.order_no}",
        request=request,
    )
    return {"request": serialize_req(req), "order_no": order.order_no, "order_id": order.id}


@router.delete("/{req_id}", summary="删除草稿采购申请")
def delete_requisition(
    req_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("requisition:manage")),
):
    req = _get(db, req_id)
    if req.status != "draft":
        raise HTTPException(status_code=400, detail="只有草稿采购申请可以删除")
    db.delete(req)
    db.commit()
    return {"ok": True}
