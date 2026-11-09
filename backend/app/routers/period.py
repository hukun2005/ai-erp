"""会计期间路由：期末结账、反结账与损益结转。"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import get_current_user, require_perm
from ..models import User
from ..services import period as period_service

router = APIRouter(prefix="/api/period", tags=["会计期间"])


class PeriodAction(BaseModel):
    period: str = ""  # YYYY-MM，留空为当月


@router.get("", summary="会计期间列表")
def list_periods(
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("ledger:view")),
):
    return period_service.list_periods(db)


@router.get("/{period}/summary", summary="期间凭证汇总（结账前校验）")
def period_summary(
    period: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("ledger:view")),
):
    return period_service.vouchers_in_period(db, period)


@router.get("/{period}/check", summary="结账前体检清单")
def period_check(
    period: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("ledger:view")),
):
    return period_service.check_period(db, period)


@router.post("/close", summary="期末结账", dependencies=[Depends(require_perm("period:manage"))])
def close_period(
    payload: PeriodAction,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("period:manage")),
):
    result = period_service.close_period(db, payload.period, user.username)
    record(
        username=user.username,
        role=user.role,
        action="期末结账",
        resource=f"期间 {result['period']}",
        detail=f"结转损益凭证 {result['carry_voucher_no'] or '（无损益发生）'}",
        request=request,
    )
    return result


@router.post("/reopen", summary="反结账", dependencies=[Depends(require_perm("period:manage"))])
def reopen_period(
    payload: PeriodAction,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("period:manage")),
):
    result = period_service.reopen_period(db, payload.period, user.username)
    record(
        username=user.username,
        role=user.role,
        action="反结账",
        resource=f"期间 {result['period']}",
        detail="重新打开期间，结转损益凭证已冲回",
        request=request,
    )
    return result
