"""数据范围隔离工具：按角色 data_scope 过滤查询，并校验记录归属。"""
from fastapi import HTTPException

from .permissions import data_scope


def own_scope(role: str) -> bool:
    return data_scope(role) == "own"


def scope_owner(user) -> str | None:
    """返回需要过滤的用户名；None 表示不限制（全部数据）。"""
    return user.username if own_scope(user.role) else None


def apply_owner_scope(stmt, column, user):
    """own 范围时追加 `column == 当前用户名` 过滤条件。"""
    owner = scope_owner(user)
    return stmt if owner is None else stmt.where(column == owner)


def ensure_owner(owner: str | None, user, action: str = "访问") -> None:
    """own 范围时校验记录归属，不属于本人则返回 403。"""
    if own_scope(user.role) and (owner or "") != user.username:
        raise HTTPException(status_code=403, detail=f"数据范围限制：只能{action}本人负责的数据")


def ensure_owner_or_public(owner: str | None, user, action: str = "操作") -> None:
    """own 范围时允许本人负责或公海（owner 为空）的记录，其余返回 403。"""
    if own_scope(user.role) and owner and owner != user.username:
        raise HTTPException(status_code=403, detail=f"数据范围限制：只能{action}本人负责的数据")
