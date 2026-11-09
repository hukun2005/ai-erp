from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..deps import get_db, require_perm
from ..services import payables, receivables

router = APIRouter(prefix="/api/reconciliation", tags=["对账总览"])


@router.get("/overview", dependencies=[Depends(require_perm("finance:view"))])
def overview(db: Session = Depends(get_db)):
    ar = receivables.customer_receivables(db)
    ap = payables.supplier_payables(db)
    ar_total = round(float(receivables.receivable_total(db)), 2)
    ap_total = round(float(payables.payable_total(db)), 2)
    return {
        "receivable_total": ar_total,
        "payable_total": ap_total,
        "net": round(ar_total - ap_total, 2),
        "receivables": ar,
        "payables": ap,
    }