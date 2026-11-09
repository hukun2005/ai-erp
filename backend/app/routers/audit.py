"""权限审计日志查询（仅管理员可见）。"""
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_perm
from ..models import AuditLog
from ..permissions import ROLE_DEFINITIONS

router = APIRouter(
    prefix="/api/audit-logs",
    tags=["审计日志"],
    dependencies=[Depends(require_perm("audit:view"))],
)

ACTION_OPTIONS = [
    "登录成功",
    "登录失败",
    "越权访问",
    "未授权访问",
    "新建用户",
    "更新用户",
    "重置密码",
    "删除用户",
    "创建销售订单",
    "销售订单状态变更",
    "删除销售订单",
    "创建采购订单",
    "采购单状态变更",
    "删除采购单",
    "库存调整",
    "创建调拨单",
    "调拨单状态变更",
    "删除调拨单",
    "导出库存报表",
    "导出库存分布",
    "下载期初模板",
    "期初导入商品",
    "期初导入客户",
    "期初导入供应商",
    "期末导出",
    "发送飞书通知",
    "飞书通知测试",
    "保存飞书配置",
    "清除飞书配置",
    "保存预警规则",
    "库存自动预警",
    "新建仓库",
    "更新仓库",
    "删除仓库",
    "新建库位",
    "更新库位",
    "删除库位",
]

USER_CHANGE_ACTIONS = ("新建用户", "更新用户", "重置密码", "删除用户")


def _out(log: AuditLog) -> dict:
    return {
        "id": log.id,
        "username": log.username,
        "role": log.role,
        "role_name": ROLE_DEFINITIONS.get(log.role, {}).get("name", log.role or "-"),
        "action": log.action,
        "resource": log.resource,
        "detail": log.detail,
        "result": log.result,
        "method": log.method,
        "path": log.path,
        "status_code": log.status_code,
        "ip": log.ip,
        "created_at": log.created_at,
    }


@router.get("/summary", summary="审计日志概览")
def summary(db: Session = Depends(get_db)):
    today_start = datetime.combine(date.today(), datetime.min.time())
    total = db.scalar(select(func.count()).select_from(AuditLog)) or 0
    today_total = db.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.created_at >= today_start)
    ) or 0
    denied_today = db.scalar(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.created_at >= today_start)
        .where(AuditLog.result == "denied")
    ) or 0
    login_failed_today = db.scalar(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.created_at >= today_start)
        .where(AuditLog.action == "登录失败")
    ) or 0
    user_changes_today = db.scalar(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.created_at >= today_start)
        .where(AuditLog.action.in_(USER_CHANGE_ACTIONS))
    ) or 0
    return {
        "total": total,
        "today_total": today_total,
        "denied_today": denied_today,
        "login_failed_today": login_failed_today,
        "user_changes_today": user_changes_today,
    }


@router.get("/actions", summary="可筛选的动作类型")
def action_options():
    return ACTION_OPTIONS


@router.get("", summary="审计日志列表")
def list_logs(
    keyword: str = Query(""),
    action: str = Query(""),
    result: str = Query(""),
    role: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = select(AuditLog)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            or_(
                AuditLog.username.like(like),
                AuditLog.action.like(like),
                AuditLog.resource.like(like),
                AuditLog.detail.like(like),
            )
        )
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if result:
        stmt = stmt.where(AuditLog.result == result)
    if role:
        stmt = stmt.where(AuditLog.role == role)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"total": total, "items": [_out(log) for log in rows]}
