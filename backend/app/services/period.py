"""会计期间管理：期末结账、反结账与损益结转。

期间一旦结账，该期间的凭证不允许再改（新增/作废/自动重生成），
并生成一张「结转损益」凭证把损益类余额转入本年利润；反结账时冲回该凭证。
"""
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import AccountPeriod, Invoice, Product, Voucher
from . import gl, payables as ap_service, receivables as ar_service


def _now_period() -> str:
    return datetime.now().strftime("%Y-%m")


def list_periods(db: Session) -> list[dict]:
    """返回所有有凭证的期间 + 当前期间，按期间倒序。"""
    periods = {
        row[0]
        for row in db.query(Voucher.period).distinct().all()
        if row[0]
    }
    periods.add(_now_period())
    closed = {
        row.period: row
        for row in db.query(AccountPeriod).filter(AccountPeriod.period.in_(periods)).all()
    }
    result = []
    for period in sorted(periods, reverse=True):
        row = closed.get(period)
        result.append(
            {
                "period": period,
                "status": row.status if row else "open",
                "carry_voucher_no": row.carry_voucher_no if row else "",
                "closed_by": row.closed_by if row else "",
                "closed_at": row.closed_at.isoformat() if row and row.closed_at else None,
                "reopened_by": row.reopened_by if row else "",
                "reopened_at": row.reopened_at.isoformat() if row and row.reopened_at else None,
            }
        )
    return result


def _get(db: Session, period: str) -> AccountPeriod | None:
    return db.query(AccountPeriod).filter(AccountPeriod.period == period).first()


def check_period(db: Session, period: str) -> dict:
    """结账前体检：未审核凭证、负库存、未核销应收应付、未认证进项。

    返回结构化清单；任意一项未通过即整体不通过，close_period 会据此阻断。
    """
    period = period or _now_period()
    items: list[dict] = []

    # 1) 未审核凭证（草稿）
    draft_count = (
        db.query(Voucher)
        .filter(Voucher.period == period, Voucher.status == "draft")
        .count()
    )
    items.append(
        {
            "key": "draft_vouchers",
            "label": "未审核凭证",
            "count": draft_count,
            "passed": draft_count == 0,
            "hint": f"{draft_count} 张草稿凭证未审核/未记账",
        }
    )

    # 2) 负库存
    negative_products = db.query(Product).filter(Product.stock_qty < 0).count()
    items.append(
        {
            "key": "negative_stock",
            "label": "负库存",
            "count": negative_products,
            "passed": negative_products == 0,
            "hint": f"{negative_products} 个商品当前库存为负",
        }
    )

    # 3) 未核销应收
    ar_items = ar_service.customer_receivables(db)
    ar_amount = round(sum(item["balance"] for item in ar_items), 2)
    items.append(
        {
            "key": "unsettled_ar",
            "label": "未核销应收",
            "count": len(ar_items),
            "amount": ar_amount,
            "passed": ar_amount <= 0.005,
            "hint": f"{len(ar_items)} 个客户应收未核销，余额 {ar_amount:,.2f}",
        }
    )

    # 4) 未核销应付
    ap_items = ap_service.supplier_payables(db)
    ap_amount = round(sum(item["balance"] for item in ap_items), 2)
    items.append(
        {
            "key": "unsettled_ap",
            "label": "未核销应付",
            "count": len(ap_items),
            "amount": ap_amount,
            "passed": ap_amount <= 0.005,
            "hint": f"{len(ap_items)} 个供应商应付未核销，余额 {ap_amount:,.2f}",
        }
    )

    # 5) 未认证进项
    uncertified = (
        db.query(Invoice)
        .filter(Invoice.direction == "purchase", Invoice.certified == False, Invoice.status != "voided")  # noqa: E712
        .count()
    )
    items.append(
        {
            "key": "uncertified_input",
            "label": "未认证进项",
            "count": uncertified,
            "passed": uncertified == 0,
            "hint": f"{uncertified} 张进项发票未认证",
        }
    )

    blocking = [item for item in items if not item["passed"]]
    return {
        "period": period,
        "passed": not blocking,
        "items": items,
        "blocking": blocking,
    }


def close_period(db: Session, period: str, username: str) -> dict:
    """结账：先体检，不通过则阻断；再结转损益并标记期间关闭。"""
    period = period or _now_period()
    row = _get(db, period)
    if row and row.status == "closed":
        raise HTTPException(status_code=400, detail=f"期间 {period} 已结账")

    # 结账前体检：未通过就不让结账
    preflight = check_period(db, period)
    if not preflight["passed"]:
        reasons = "；".join(item["hint"] for item in preflight["blocking"])
        raise HTTPException(
            status_code=400,
            detail=f"结账前体检未通过：{reasons}",
        )

    try:
        carry = gl.carry_forward_voucher(db, period, username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 12 月结账：在结转损益之后，再把全年损益（本年利润）转入未分配利润
    year_carry = None
    if period.endswith("-12"):
        year_carry = gl.carry_forward_year(db, period[:4], username)

    if row is None:
        row = AccountPeriod(period=period, status="closed")
        db.add(row)
    row.status = "closed"
    row.carry_voucher_no = carry.voucher_no if carry else ""
    row.closed_by = username
    row.closed_at = datetime.now()
    row.reopened_by = ""
    row.reopened_at = None
    db.commit()
    db.refresh(row)
    return {
        "period": period,
        "status": "closed",
        "carry_voucher_no": row.carry_voucher_no,
        "year_carry_voucher_no": year_carry.voucher_no if year_carry else "",
    }


def reopen_period(db: Session, period: str, username: str) -> dict:
    """反结账：作废结转损益凭证 → 标记期间重新打开。"""
    period = period or _now_period()
    row = _get(db, period)
    if not row or row.status != "closed":
        raise HTTPException(status_code=400, detail=f"期间 {period} 未结账，无需反结账")
    # 冲回该期间所有结转类凭证（月末损益结转 + 12 月年结）
    closing_rows = (
        db.query(Voucher)
        .filter(Voucher.period == period, Voucher.source_type == "closing", Voucher.status == "posted")
        .all()
    )
    for carry in closing_rows:
        carry.status = "voided"
    row.status = "open"
    row.reopened_by = username
    row.reopened_at = datetime.now()
    db.commit()
    db.refresh(row)
    return {"period": period, "status": "open"}


def ensure_open(db: Session, period: str) -> None:
    """供凭证写入前调用：已结账期间抛 400。"""
    if gl.period_closed(db, period):
        raise HTTPException(status_code=400, detail=f"期间 {period} 已结账，请先反结账")


def vouchers_in_period(db: Session, period: str) -> dict:
    """结账前校验用：返回该期间凭证数与借贷合计。"""
    rows = db.query(
        func.count(Voucher.id),
        func.coalesce(func.sum(Voucher.total_debit), 0),
        func.coalesce(func.sum(Voucher.total_credit), 0),
    ).filter(Voucher.period == period, Voucher.status == "posted").first()
    return {
        "period": period,
        "count": int(rows[0] or 0),
        "total_debit": round(float(rows[1] or 0), 2),
        "total_credit": round(float(rows[2] or 0), 2),
    }
