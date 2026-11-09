"""站内消息：审批提交、审批超时、库存预警统一收口。

写 notifications 表，按用户维度落库；与飞书通知并存互不影响。
目标用户定位：按角色 → 权限反查活跃用户（admin 持有 "*"，has_permission 恒为真）。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Notification, User
from ..permissions import ROLE_DEFINITIONS, has_permission

CATEGORY_LABELS = {
    "approval": "审批",
    "sla": "超时",
    "stock": "库存预警",
}


def roles_with_permission(permission: str) -> list[str]:
    """拥有某权限的所有角色（含 admin）。"""
    return [role for role in ROLE_DEFINITIONS if has_permission(role, permission)]


def _active_users(db: Session, roles: list[str]) -> list[User]:
    if not roles:
        return []
    return list(
        db.scalars(
            select(User).where(User.role.in_(roles)).where(User.is_active == True)  # noqa: E712
        ).all()
    )


def users_by_permission(db: Session, permission: str) -> list[User]:
    """按权限反查目标用户（用于「谁审批谁收到」「谁管库存谁收到」）。"""
    return _active_users(db, roles_with_permission(permission))


def users_by_role(db: Session, role: str) -> list[User]:
    return _active_users(db, [role])


def push(
    db: Session,
    users: list[User],
    *,
    category: str,
    title: str,
    body: str = "",
    ref_type: str = "",
    ref_id: int = 0,
) -> int:
    """给一组用户各写一条站内消息；返回实际写入条数。调用方负责 commit。"""
    seen: set[int] = set()
    count = 0
    for user in users:
        if user is None or user.id in seen:
            continue
        seen.add(user.id)
        db.add(
            Notification(
                user_id=user.id,
                category=category,
                title=(title or "")[:120],
                body=body or "",
                ref_type=ref_type or "",
                ref_id=int(ref_id or 0),
            )
        )
        count += 1
    # 不主动 commit：由调用方在其事务里提交，保证与审批日志/去重状态原子。
    db.flush()
    return count


def unread_count(db: Session, user_id: int) -> int:
    from sqlalchemy import func

    return (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id)
            .where(Notification.is_read == False)  # noqa: E712
        )
        or 0
    )