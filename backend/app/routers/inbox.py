"""站内消息中心：顶栏铃铛读取未读与消息列表。

任何登录用户都能读自己的消息，因此统一用 get_current_user，不做权限过滤。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Notification, User
from ..services import inbox as inbox_service

router = APIRouter(prefix="/api/inbox", tags=["站内消息"])


def _serialize(row: Notification) -> dict:
    return {
        "id": row.id,
        "category": row.category,
        "category_label": inbox_service.CATEGORY_LABELS.get(row.category, row.category),
        "title": row.title,
        "body": row.body,
        "ref_type": row.ref_type,
        "ref_id": row.ref_id,
        "is_read": bool(row.is_read),
        "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else "",
    }


@router.get("", summary="我的站内消息列表")
def list_messages(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    unread_only: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = (
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.id.desc())
        .offset(offset)
        .limit(limit)
    )
    if unread_only:
        query = query.where(Notification.is_read == False)  # noqa: E712
    rows = list(db.scalars(query).all())
    return {
        "total": inbox_service.unread_count(db, user.id),
        "items": [_serialize(row) for row in rows],
    }


@router.get("/unread-count", summary="未读消息数（顶栏红点）")
def unread_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return {"unread": inbox_service.unread_count(db, user.id)}


@router.post("/{notification_id}/read", summary="标记单条为已读")
def mark_read(
    notification_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.get(Notification, notification_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="消息不存在")
    row.is_read = True
    db.commit()
    return {"ok": True}


@router.post("/read-all", summary="全部标记为已读")
def mark_all_read(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.scalars(
        select(Notification).where(Notification.user_id == user.id).where(Notification.is_read == False)  # noqa: E712
    ).all()
    for row in rows:
        row.is_read = True
    db.commit()
    return {"ok": True, "count": len(rows)}