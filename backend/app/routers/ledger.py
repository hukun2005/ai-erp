"""总账路由：会计科目、记账凭证、三大财务报表。"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_perm
from ..models import Account, User, Voucher, VoucherLine
from ..services import gl, period as period_service

router = APIRouter(prefix="/api/ledger", tags=["总账"], dependencies=[Depends(require_perm("ledger:view"))])


# ---------- 请求模型 ----------
class AccountCreate(BaseModel):
    code: str
    name: str
    category: str = "资产"
    direction: str = "dr"
    parent_code: str = ""


class VoucherLineIn(BaseModel):
    account_code: str
    summary: str = ""
    debit: float = Field(0.0, ge=0)
    credit: float = Field(0.0, ge=0)
    partner_name: str = ""


class VoucherCreate(BaseModel):
    voucher_date: datetime
    summary: str = ""
    lines: list[VoucherLineIn]


def _account_out(a: Account) -> dict:
    return {
        "id": a.id, "code": a.code, "name": a.name, "category": a.category,
        "direction": a.direction, "parent_code": a.parent_code, "level": a.level,
        "is_active": a.is_active,
    }


def _voucher_out(v: Voucher, lines: list[VoucherLine]) -> dict:
    return {
        "id": v.id, "voucher_no": v.voucher_no, "period": v.period,
        "voucher_date": v.voucher_date.isoformat() if v.voucher_date else None,
        "summary": v.summary, "total_debit": v.total_debit, "total_credit": v.total_credit,
        "status": v.status, "source_type": v.source_type, "source_no": v.source_no,
        "created_by": v.created_by,
        "lines": [
            {
                "id": ln.id, "account_code": ln.account_code, "account_name": ln.account_name,
                "summary": ln.summary, "debit": ln.debit, "credit": ln.credit,
                "partner_name": ln.partner_name,
            }
            for ln in lines
        ],
    }


# ---------- 会计科目 ----------
@router.get("/accounts", summary="会计科目列表")
def list_accounts(db: Session = Depends(get_db)):
    accounts = db.query(Account).order_by(Account.code).all()
    return [_account_out(a) for a in accounts]


@router.post("/accounts", summary="新增会计科目", dependencies=[Depends(require_perm("ledger:manage"))])
def create_account(body: AccountCreate, db: Session = Depends(get_db)):
    if db.query(Account).filter(Account.code == body.code).first():
        raise HTTPException(status_code=400, detail="科目编码已存在")
    a = Account(
        code=body.code, name=body.name, category=body.category,
        direction=body.direction, parent_code=body.parent_code,
        level=1 if not body.parent_code else 2,
    )
    db.add(a)
    db.commit()
    return _account_out(a)


# ---------- 记账凭证 ----------
@router.get("/vouchers", summary="凭证列表")
def list_vouchers(period: str = Query(""), db: Session = Depends(get_db)):
    q = db.query(Voucher)
    if period:
        q = q.filter(Voucher.period == period)
    vouchers = q.order_by(Voucher.voucher_date.desc(), Voucher.id.desc()).all()
    ids = [v.id for v in vouchers]
    lines_by_voucher: dict[int, list[VoucherLine]] = {}
    if ids:
        for ln in db.query(VoucherLine).filter(VoucherLine.voucher_id.in_(ids)).order_by(VoucherLine.id).all():
            lines_by_voucher.setdefault(ln.voucher_id, []).append(ln)
    return [_voucher_out(v, lines_by_voucher.get(v.id, [])) for v in vouchers]


@router.post("/vouchers", summary="手工新增凭证", dependencies=[Depends(require_perm("ledger:manage"))])
def create_voucher(body: VoucherCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not body.lines:
        raise HTTPException(status_code=400, detail="凭证至少需要一条分录")
    accts = {a.code: a for a in db.query(Account).all()}
    total_debit = round(sum(l.debit for l in body.lines), 2)
    total_credit = round(sum(l.credit for l in body.lines), 2)
    if abs(total_debit - total_credit) > 0.005 or total_debit == 0:
        raise HTTPException(status_code=400, detail="借贷必须平衡且金额大于 0")

    period = body.voucher_date.strftime("%Y-%m")
    period_service.ensure_open(db, period)
    count = db.query(Voucher).filter(Voucher.period == period).count()
    v = Voucher(
        voucher_no=f"记-{period}-{count + 1:04d}",
        period=period, voucher_date=body.voucher_date, summary=body.summary,
        total_debit=total_debit, total_credit=total_credit,
        status="posted", source_type="manual", created_by=user.username,
    )
    db.add(v)
    db.flush()
    for ln in body.lines:
        acct = accts.get(ln.account_code)
        if acct is None:
            raise HTTPException(status_code=400, detail=f"科目编码不存在：{ln.account_code}")
        db.add(VoucherLine(
            voucher_id=v.id, account_code=ln.account_code, account_name=acct.name,
            summary=ln.summary, debit=ln.debit, credit=ln.credit, partner_name=ln.partner_name,
        ))
    db.commit()
    lines = db.query(VoucherLine).filter(VoucherLine.voucher_id == v.id).all()
    return _voucher_out(v, lines)


@router.post("/vouchers/{voucher_id}/void", summary="作废凭证", dependencies=[Depends(require_perm("ledger:manage"))])
def void_voucher(voucher_id: int, db: Session = Depends(get_db)):
    v = db.query(Voucher).filter(Voucher.id == voucher_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="凭证不存在")
    if v.status == "voided":
        raise HTTPException(status_code=400, detail="凭证已作废")
    period_service.ensure_open(db, v.period)
    v.status = "voided"
    db.commit()
    return {"ok": True}


@router.post("/vouchers/generate", summary="按期间自动生成凭证", dependencies=[Depends(require_perm("ledger:manage"))])
def generate_vouchers(period: str = Query("", description="YYYY-MM，留空为当月"),
                      db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not period:
        period = datetime.now().strftime("%Y-%m")
    try:
        result = gl.generate_vouchers(db, period, user.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


# ---------- 财务报表 ----------
@router.get("/statements/balance-sheet", summary="资产负债表")
def balance_sheet(period: str = Query(""), db: Session = Depends(get_db)):
    return gl.balance_sheet(db, period)


@router.get("/statements/income-statement", summary="利润表")
def income_statement(period: str = Query(""), db: Session = Depends(get_db)):
    return gl.income_statement(db, period)


@router.get("/statements/cash-flow", summary="现金流量表")
def cash_flow(period: str = Query(""), db: Session = Depends(get_db)):
    return gl.cash_flow_statement(db, period)