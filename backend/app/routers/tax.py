"""税务报表：进项/销项税额明细与汇总（价税分离口径）。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_perm
from ..models import Invoice, User

router = APIRouter(prefix="/api/tax", tags=["税务报表"])


@router.get("/report", summary="进项销项税额汇总", dependencies=[Depends(require_perm("finance:view"))])
def tax_report(
    period: str = Query("", description="YYYY-MM，留空为全部"),
    db: Session = Depends(get_db),
):
    stmt = (
        select(
            Invoice.direction,
            func.coalesce(func.sum(Invoice.tax_amount), 0),
            func.coalesce(func.sum(Invoice.total_amount), 0),
            func.count(Invoice.id),
        )
        .where(Invoice.status != "voided")
    )
    if period:
        stmt = stmt.where(func.strftime("%Y-%m", Invoice.issue_date) == period)
    rows = db.execute(stmt.group_by(Invoice.direction)).all()

    by_dir = {d: {"tax": round(float(t), 2), "total": round(float(s), 2), "count": int(c)} for d, t, s, c in rows}
    sales = by_dir.get("sales", {"tax": 0.0, "total": 0.0, "count": 0})
    purchase = by_dir.get("purchase", {"tax": 0.0, "total": 0.0, "count": 0})

    # 进项可抵扣税额：仅已认证的进项发票
    certified_stmt = select(func.coalesce(func.sum(Invoice.tax_amount), 0)).where(
        Invoice.direction == "purchase",
        Invoice.status != "voided",
        Invoice.certified.is_(True),
    )
    if period:
        certified_stmt = certified_stmt.where(func.strftime("%Y-%m", Invoice.issue_date) == period)
    certified_tax = round(float(db.scalar(certified_stmt) or 0.0), 2)

    sales_tax = sales["tax"]
    payable = round(sales_tax - certified_tax, 2)
    return {
        "period": period or "全部",
        "sales_tax": sales_tax,          # 销项税额
        "purchase_tax": purchase["tax"],  # 进项税额（含未认证）
        "certified_purchase_tax": certified_tax,  # 已认证可抵扣进项
        "tax_payable": payable,           # 应纳税额（销项 - 可抵扣进项）
        "sales_total": sales["total"],
        "purchase_total": purchase["total"],
        "sales_count": sales["count"],
        "purchase_count": purchase["count"],
    }


@router.get("/detail", summary="税额明细（按发票）", dependencies=[Depends(require_perm("finance:view"))])
def tax_detail(
    direction: str | None = Query(None),
    period: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = select(Invoice).where(Invoice.status != "voided")
    if direction:
        stmt = stmt.where(Invoice.direction == direction)
    if period:
        stmt = stmt.where(func.strftime("%Y-%m", Invoice.issue_date) == period)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Invoice.issue_date.desc(), Invoice.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = [
        {
            "id": inv.id,
            "invoice_no": inv.invoice_no,
            "direction": inv.direction,
            "partner_name": inv.partner_name,
            "order_no": inv.order_no,
            "amount": round(float(inv.amount), 2),
            "tax_rate": inv.tax_rate,
            "tax_amount": round(float(inv.tax_amount), 2),
            "total_amount": round(float(inv.total_amount), 2),
            "certified": inv.certified,
            "issue_date": inv.issue_date.strftime("%Y-%m-%d") if inv.issue_date else "",
            "status": inv.status,
        }
        for inv in rows
    ]
    return {"total": total, "items": items}
