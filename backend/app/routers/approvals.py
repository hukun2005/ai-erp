"""待审批单据列表与审批轨迹查询（支持任意单据类型）。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..permissions import has_permission
from ..services import approval_flow, approval_sla, approvals

router = APIRouter(prefix="/api/approvals", tags=["订单审批"])


def _can_approve(user: User, order_type: str) -> bool:
    """按单据类型取默认审批权限；规则里也可覆盖成自定义权限。"""
    perm = approval_flow.DEFAULT_PERMISSIONS.get(order_type, "")
    return bool(perm) and has_permission(user.role, perm)


@router.get("/pending", summary="待审批单据列表")
def list_pending(
    type: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # 有该单据类型审批权限的人，或作为某笔委托的代理人，都能看到待办
    items = [
        row for row in approvals.pending_orders(db)
        if _can_approve(user, row["order_type"])
        or approval_flow.is_delegatee(db, row["order_type"], user.username)
    ]
    if type:
        items = [row for row in items if row["order_type"] == type]
    # 补上「已等待多久 / 是否超时」，前端据此打标签
    approval_sla.overdue_rows(db, items)

    stats = {
        "sales": sum(1 for row in items if row["order_type"] == "sales"),
        "purchase": sum(1 for row in items if row["order_type"] == "purchase"),
        "requisition": sum(1 for row in items if row["order_type"] == "requisition"),
        "overdue": sum(1 for row in items if row.get("overdue")),
    }
    total = len(items)
    start = (page - 1) * page_size
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "stats": stats,
        "items": items[start : start + page_size],
    }


@router.get("/{order_type}/{order_id}/logs", summary="单据审批轨迹")
def list_logs(
    order_type: str,
    order_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if order_type not in approval_flow.DOC_LABELS:
        raise HTTPException(status_code=400, detail="单据类型不支持")
    if not _can_approve(user, order_type) and not approval_flow.is_delegatee(
        db, order_type, user.username
    ):
        raise HTTPException(status_code=403, detail="当前角色无此单据的审批权限")
    rows = approvals.order_logs(db, order_type, order_id)
    total = len(rows)
    start = (page - 1) * page_size
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": row.id,
                "order_type": row.order_type,
                "order_id": row.order_id,
                "action": row.action,
                "operator": row.operator,
                "principal": row.principal or row.operator,
                "role": row.role,
                "level": row.level,
                "comment": row.comment,
                "created_at": row.created_at,
            }
            for row in rows[start : start + page_size]
        ],
    }
