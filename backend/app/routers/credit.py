"""客户信用额度查询接口。

额度是客户主数据的一部分，但「已占用多少」得按应收口径实时算，
所以单独开一个只读接口，避免在客户列表里塞进一堆聚合查询。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_perm
from ..models import Customer, User
from ..scoping import ensure_owner_or_public
from ..services import credit

router = APIRouter(prefix="/api/customers", tags=["客户"])


@router.get("/{customer_id}/credit", summary="客户信用额度使用情况")
def customer_credit(
    customer_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("customer:view")),
):
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="客户不存在")
    ensure_owner_or_public(customer.owner, user, "查看")
    return credit.summary(db, customer)
