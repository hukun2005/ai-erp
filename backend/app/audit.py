"""权限审计日志。

两种写入方式：
1. record() —— 业务接口内显式记录关键操作（登录、订单流转、用户变更、库存调整）；
2. AuditMiddleware —— 自动记录接口返回的 401/403，捕获越权访问与凭证失效。

审计日志使用独立数据库会话写入，不参与业务事务，业务回滚时日志依然保留。
"""
from __future__ import annotations

import logging

from fastapi import Request
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from .database import SessionLocal
from .models import AuditLog, User
from .security import decode_token

logger = logging.getLogger(__name__)

# 自动捕获不处理的路径（登录失败已由登录接口显式记录，避免重复）
AUTO_SKIP_PATHS = {"/api/health", "/api/auth/login"}


def _client_ip(request: Request | None) -> str:
    if request is None:
        return ""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:50]
    return (request.client.host if request.client else "")[:50]


def record(
    *,
    username: str,
    action: str,
    role: str = "",
    resource: str = "",
    detail: str = "",
    result: str = "success",
    request: Request | None = None,
    status_code: int = 200,
) -> None:
    """写入一条审计日志；任何异常都不影响主业务流程。"""
    db: Session = SessionLocal()
    try:
        db.add(
            AuditLog(
                username=username or "",
                role=role or "",
                action=action,
                resource=resource,
                detail=(detail or "")[:2000],
                result=result,
                method=request.method if request else "",
                path=str(request.url.path)[:200] if request else "",
                status_code=status_code,
                ip=_client_ip(request),
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("写入审计日志失败：%s", action)
    finally:
        db.close()


def _identity(request: Request) -> tuple[str, str]:
    """从 JWT 中解析操作人，解析失败按匿名处理。"""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return "匿名", ""
    payload = decode_token(header[7:].strip())
    if not payload:
        return "匿名", ""
    db = SessionLocal()
    try:
        user = db.get(User, int(payload.get("sub", 0)))
        if user:
            return user.username, user.role
    except (TypeError, ValueError):
        pass
    finally:
        db.close()
    return str(payload.get("username") or "匿名"), ""


class AuditMiddleware(BaseHTTPMiddleware):
    """自动记录 401/403，用于追踪越权访问。"""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if (
            path.startswith("/api")
            and path not in AUTO_SKIP_PATHS
            and response.status_code in (401, 403)
        ):
            username, role = _identity(request)
            denied = response.status_code == 403
            record(
                username=username,
                role=role,
                action="越权访问" if denied else "未授权访问",
                resource=f"{request.method} {path}"[:100],
                detail="当前角色无权限执行该操作" if denied else "未登录或登录凭证失效",
                result="denied",
                request=request,
                status_code=response.status_code,
            )
        return response
