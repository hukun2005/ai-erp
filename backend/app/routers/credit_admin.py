"""信用策略与超额特批接口。"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import require_perm
from ..models import CreditOverride, SalesOrder, User
from ..schemas import CreditConfigIn, CreditOverrideAction, CreditOverrideCreate
from ..services import credit as credit_service
from ..services import credit_policy

router = APIRouter(prefix="/api/credit", tags=["信用策略"])


def _serialize_override(row: CreditOverride) -> dict:
    return {
        "id": row.id,
        "order_id": row.order_id,
        "order_type": row.order_type,
        "customer_id": row.customer_id,
        "amount": row.amount,
        "over_amount": row.over_amount,
        "reason": row.reason,
        "status": row.status,
        "requested_by": row.requested_by,
        "requested_at": row.requested_at,
        "decided_by": row.decided_by,
        "decided_at": row.decided_at,
        "comment": row.comment,
    }


@router.get("/config", summary="信用策略配置", dependencies=[Depends(require_perm("credit:manage"))])
def get_config(
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("credit:manage")),
):
    return credit_policy.list_policies(db)


@router.put("/config", summary="保存信用策略")
def save_config(
    payload: CreditConfigIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("credit:manage")),
):
    if not payload.policies:
        raise HTTPException(status_code=400, detail="请至少配置一个客户等级的策略")
    result = credit_policy.save_policies(db, payload.policies, user.username)
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="修改信用策略",
        resource="信用策略",
        detail="、".join(f"{level}={policy}" for level, policy in payload.policies.items()),
        request=request,
    )
    return result


@router.get("/overrides", summary="特批申请列表", dependencies=[Depends(require_perm("credit:manage"))])
def list_overrides(
    status: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("credit:manage")),
):
    stmt = select(CreditOverride).order_by(CreditOverride.id.desc())
    if status:
        stmt = stmt.where(CreditOverride.status == status)
    rows = db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    total = db.scalar(select(func.count()).select_from(CreditOverride)) or 0
    return {"total": total, "items": [_serialize_override(row) for row in rows]}


@router.post("/overrides", summary="发起超额特批")
def request_override(
    payload: CreditOverrideCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("sales:manage")),
):
    order = db.get(SalesOrder, payload.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="销售订单不存在")
    if order.status not in ("draft", "pending", "confirmed"):
        raise HTTPException(status_code=400, detail="当前订单状态不支持申请特批")

    check = credit_service.check_order(db, order)
    if not check.get("blocked"):
        raise HTTPException(status_code=400, detail="该订单未触发信用拦截，无需特批")

    row = credit_policy.request_override(
        db, order, user, payload.reason, check.get("over_amount", 0)
    )
    db.commit()
    db.refresh(row)
    record(
        username=user.username,
        role=user.role,
        action="申请信用特批",
        resource=f"销售订单 {order.order_no}",
        detail=check.get("message", ""),
        request=request,
    )
    return _serialize_override(row)


@router.post("/overrides/{override_id}/status", summary="审批特批申请")
def decide_override(
    override_id: int,
    payload: CreditOverrideAction,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("credit:manage")),
):
    row = db.get(CreditOverride, override_id)
    if not row:
        raise HTTPException(status_code=404, detail="特批申请不存在")
    credit_policy.decide_override(db, row, payload.action, user, payload.comment)
    db.commit()
    db.refresh(row)
    record(
        username=user.username,
        role=user.role,
        action="审批信用特批",
        resource=f"特批 {row.id}",
        detail=("通过" if row.status == "approved" else "驳回") + (f"：{row.comment}" if row.comment else ""),
        request=request,
    )
    return _serialize_override(row)


@router.get("/summary/{customer_id}", summary="客户信用额度全景")
def credit_summary(
    customer_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("customer:view")),
):
    from ..models import Customer

    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="客户不存在")
    return credit_service.summary(db, customer)
