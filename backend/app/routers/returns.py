from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import require_perm
from ..models import Customer, PurchaseOrder, PurchaseReturn, SalesOrder, SalesReturn, Supplier, User
from ..schemas import PurchaseReturnCreate, SalesReturnCreate, StatusUpdate
from ..scoping import apply_owner_scope, ensure_owner
from ..services import approval_flow, approvals
from ..services import returns as service
from ..services.returns import serialize_return

router = APIRouter(prefix="/api/returns", tags=["退货管理"])


def _get_sales(db: Session, return_id: int) -> SalesReturn:
    ret = db.get(SalesReturn, return_id)
    if not ret:
        raise HTTPException(status_code=404, detail="销售退货单不存在")
    return ret


def _get_purchase(db: Session, return_id: int) -> PurchaseReturn:
    ret = db.get(PurchaseReturn, return_id)
    if not ret:
        raise HTTPException(status_code=404, detail="采购退货单不存在")
    return ret


# ---------------- 销售退货 ----------------
@router.get("/sales", summary="销售退货列表")
def list_sales_returns(
    keyword: str = Query(""),
    status: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("return:view")),
):
    stmt = select(SalesReturn).join(Customer)
    stmt = apply_owner_scope(stmt, SalesReturn.created_by, user)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(SalesReturn.return_no.like(like), Customer.name.like(like)))
    if status:
        stmt = stmt.where(SalesReturn.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(SalesReturn.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"total": total, "items": [serialize_return(row, "sales") for row in rows]}


@router.get("/sales/order/{order_id}/items", summary="销售订单可退明细")
def sales_returnable(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("return:view")),
):
    order = db.get(SalesOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="销售订单不存在")
    ensure_owner(order.created_by, user, "退货")
    return {
        "order_id": order.id,
        "order_no": order.order_no,
        "customer_id": order.customer_id,
        "customer_name": order.customer.name if order.customer else "",
        "status": order.status,
        "items": service.sales_returnable_items(db, order),
    }


@router.get("/sales/{return_id}", summary="销售退货详情")
def get_sales_return(
    return_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("return:view")),
):
    ret = _get_sales(db, return_id)
    ensure_owner(ret.created_by, user, "查看")
    return serialize_return(ret, "sales")


@router.post("/sales", summary="创建销售退货单（草稿）")
def create_sales_return(
    payload: SalesReturnCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("return:manage")),
):
    order = db.get(SalesOrder, payload.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="销售订单不存在")
    if not payload.items:
        raise HTTPException(status_code=400, detail="退货明细不能为空")
    ret = service.build_sales_return(db, payload, user, order, payload.warehouse_id)
    db.commit()
    db.refresh(ret)
    record(
        username=user.username,
        role=user.role,
        action="创建销售退货单",
        resource=f"销售退货 {ret.return_no}",
        detail=f"原单 {order.order_no}，金额 {ret.total_amount:,.2f}",
        request=request,
    )
    return serialize_return(ret, "sales")


@router.post("/sales/{return_id}/status", summary="确认/取消销售退货")
def change_sales_status(
    return_id: int,
    payload: StatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("return:manage")),
):
    ret = _get_sales(db, return_id)
    ensure_owner(ret.created_by, user, "操作")
    action = payload.action
    if action == "submit":
        approvals.ensure_status(ret, ("draft",), "提交审批")
        ret.approval_required = approval_flow.required_levels(
            db, "sales_return", ret.total_amount, approval_flow.context_for(db, "sales_return", ret)
        )
        ret.approval_approved = 0
        ret.status = "pending"
        approvals.log_action(db, "sales_return", ret.id, "submit", user, payload.comment)
    elif action == "approve":
        approvals.ensure_status(ret, ("pending",), "审批通过")
        result = approval_flow.perform_approval(db, "sales_return", ret, user, payload.comment)
        if result["completed"]:
            ret.status = "draft"
            service.confirm_sales_return(db, ret, user)
        else:
            db.commit()
            db.refresh(ret)
            data = serialize_return(ret, "sales")
            data["pending_next_level"] = result["level_complete"]
            return data
    elif action == "reject":
        approvals.ensure_status(ret, ("pending",), "审批驳回")
        level, _rule = approval_flow.ensure_level_permission(db, "sales_return", ret, user)
        ret.status = "draft"
        ret.approval_approved = 0
        approvals.log_action(
            db, "sales_return", ret.id, "reject", user, payload.comment or "驳回", level=level
        )
    elif action == "confirm":
        if approval_flow.has_rules(db, "sales_return"):
            raise HTTPException(
                status_code=400, detail="已配置退货审批规则，请先提交审批，审批通过后自动确认"
            )
        service.confirm_sales_return(db, ret, user)
    elif action == "cancel":
        service.cancel_sales_return(db, ret, user)
    else:
        raise HTTPException(status_code=400, detail=f"不支持的操作：{action}")

    db.commit()
    db.refresh(ret)
    record(
        username=user.username,
        role=user.role,
        action="销售退货状态变更",
        resource=f"销售退货 {ret.return_no}",
        detail=f"操作={action}" + (f"：{payload.comment}" if payload.comment else ""),
        request=request,
    )
    return serialize_return(ret, "sales")


@router.delete("/sales/{return_id}", summary="删除草稿销售退货单")
def delete_sales_return(
    return_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("return:manage")),
):
    ret = _get_sales(db, return_id)
    ensure_owner(ret.created_by, user, "删除")
    if ret.status != "draft":
        raise HTTPException(status_code=400, detail="仅草稿退货单可删除")
    return_no = ret.return_no
    db.delete(ret)
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="删除销售退货单",
        resource=f"销售退货 {return_no}",
        detail="删除草稿退货单",
        request=request,
    )
    return {"ok": True}


# ---------------- 采购退货 ----------------
@router.get("/purchase", summary="采购退货列表")
def list_purchase_returns(
    keyword: str = Query(""),
    status: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("return:view")),
):
    stmt = select(PurchaseReturn).join(Supplier)
    stmt = apply_owner_scope(stmt, PurchaseReturn.created_by, user)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(PurchaseReturn.return_no.like(like), Supplier.name.like(like)))
    if status:
        stmt = stmt.where(PurchaseReturn.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(PurchaseReturn.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"total": total, "items": [serialize_return(row, "purchase") for row in rows]}


@router.get("/purchase/order/{order_id}/items", summary="采购订单可退明细")
def purchase_returnable(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("return:view")),
):
    order = db.get(PurchaseOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    ensure_owner(order.created_by, user, "退货")
    return {
        "order_id": order.id,
        "order_no": order.order_no,
        "supplier_id": order.supplier_id,
        "supplier_name": order.supplier.name if order.supplier else "",
        "status": order.status,
        "items": service.purchase_returnable_items(db, order),
    }


@router.get("/purchase/{return_id}", summary="采购退货详情")
def get_purchase_return(
    return_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("return:view")),
):
    ret = _get_purchase(db, return_id)
    ensure_owner(ret.created_by, user, "查看")
    return serialize_return(ret, "purchase")


@router.post("/purchase", summary="创建采购退货单（草稿）")
def create_purchase_return(
    payload: PurchaseReturnCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("return:manage")),
):
    order = db.get(PurchaseOrder, payload.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    if not payload.items:
        raise HTTPException(status_code=400, detail="退货明细不能为空")
    ret = service.build_purchase_return(db, payload, user, order, payload.warehouse_id)
    db.commit()
    db.refresh(ret)
    record(
        username=user.username,
        role=user.role,
        action="创建采购退货单",
        resource=f"采购退货 {ret.return_no}",
        detail=f"原单 {order.order_no}，金额 {ret.total_amount:,.2f}",
        request=request,
    )
    return serialize_return(ret, "purchase")


@router.post("/purchase/{return_id}/status", summary="确认/取消采购退货")
def change_purchase_status(
    return_id: int,
    payload: StatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("return:manage")),
):
    ret = _get_purchase(db, return_id)
    ensure_owner(ret.created_by, user, "操作")
    action = payload.action
    if action == "submit":
        approvals.ensure_status(ret, ("draft",), "提交审批")
        ret.approval_required = approval_flow.required_levels(
            db, "purchase_return", ret.total_amount, approval_flow.context_for(db, "purchase_return", ret)
        )
        ret.approval_approved = 0
        ret.status = "pending"
        approvals.log_action(db, "purchase_return", ret.id, "submit", user, payload.comment)
    elif action == "approve":
        approvals.ensure_status(ret, ("pending",), "审批通过")
        result = approval_flow.perform_approval(db, "purchase_return", ret, user, payload.comment)
        if result["completed"]:
            ret.status = "draft"
            service.confirm_purchase_return(db, ret, user)
        else:
            db.commit()
            db.refresh(ret)
            data = serialize_return(ret, "purchase")
            data["pending_next_level"] = result["level_complete"]
            return data
    elif action == "reject":
        approvals.ensure_status(ret, ("pending",), "审批驳回")
        level, _rule = approval_flow.ensure_level_permission(db, "purchase_return", ret, user)
        ret.status = "draft"
        ret.approval_approved = 0
        approvals.log_action(
            db, "purchase_return", ret.id, "reject", user, payload.comment or "驳回", level=level
        )
    elif action == "confirm":
        if approval_flow.has_rules(db, "purchase_return"):
            raise HTTPException(
                status_code=400, detail="已配置退货审批规则，请先提交审批，审批通过后自动确认"
            )
        service.confirm_purchase_return(db, ret, user)
    elif action == "cancel":
        service.cancel_purchase_return(db, ret, user)
    else:
        raise HTTPException(status_code=400, detail=f"不支持的操作：{action}")

    db.commit()
    db.refresh(ret)
    record(
        username=user.username,
        role=user.role,
        action="采购退货状态变更",
        resource=f"采购退货 {ret.return_no}",
        detail=f"操作={action}" + (f"：{payload.comment}" if payload.comment else ""),
        request=request,
    )
    return serialize_return(ret, "purchase")


@router.delete("/purchase/{return_id}", summary="删除草稿采购退货单")
def delete_purchase_return(
    return_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("return:manage")),
):
    ret = _get_purchase(db, return_id)
    ensure_owner(ret.created_by, user, "删除")
    if ret.status != "draft":
        raise HTTPException(status_code=400, detail="仅草稿退货单可删除")
    return_no = ret.return_no
    db.delete(ret)
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="删除采购退货单",
        resource=f"采购退货 {return_no}",
        detail="删除草稿退货单",
        request=request,
    )
    return {"ok": True}
