"""审批超时规则：待审批超过 remind_hours 小时先提醒，仍超时且配置了
escalate_hours 则升级到上级。

扫描由内置调度器定期触发（APPROVAL_SLA_SCAN_MINUTES），也可以在
「审批与信用 → 超时规则」里手动点「立即巡检」。

设计取舍：超时只做「提醒 / 升级通知」，不代替审批人签字。
财务类单据（付款、收退货、盘点差异）如果系统自动通过，账实责任会失真，
所以升级动作是：记录一条 escalate 日志 + 推送给有该单据最高审批权限的人，
由人来做最终决定。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import ApprovalLog, ApprovalSla
from . import approval_flow, approvals

# 默认：所有单据 24 小时提醒；升级小时默认不开启（空），界面上按需配置
DEFAULT_REMIND_HOURS = 24


def _doc_types() -> list[str]:
    return list(approval_flow.DOC_LABELS.keys())


def list_configs(db: Session) -> list[dict]:
    """返回所有单据类型的超时配置（未配置过的用默认值占位）。"""
    saved = {row.doc_type: row for row in db.scalars(select(ApprovalSla)).all()}
    rows = []
    for doc_type in _doc_types():
        config = saved.get(doc_type)
        rows.append(
            {
                "doc_type": doc_type,
                "label": approval_flow.DOC_LABELS.get(doc_type, doc_type),
                "remind_hours": int(config.remind_hours) if config and config.remind_hours is not None else DEFAULT_REMIND_HOURS,
                "escalate_hours": int(config.escalate_hours) if config and config.escalate_hours else None,
                "enabled": bool(config.enabled) if config else True,
                "configured": config is not None,
                "updated_by": config.updated_by if config else "",
                "updated_at": config.updated_at if config else None,
            }
        )
    return rows


def save_config(
    db: Session,
    doc_type: str,
    *,
    remind_hours: int,
    escalate_hours: int | None,
    enabled: bool,
    username: str,
) -> dict:
    if doc_type not in _doc_types():
        raise ValueError("单据类型不支持")
    if remind_hours < 1 or remind_hours > 720:
        raise ValueError("提醒小时数需在 1–720 之间")
    if escalate_hours is not None and (escalate_hours < 1 or escalate_hours > 720):
        raise ValueError("升级小时数需在 1–720 之间")
    if escalate_hours is not None and escalate_hours <= remind_hours:
        raise ValueError("升级小时数必须大于提醒小时数")
    config = db.scalar(select(ApprovalSla).where(ApprovalSla.doc_type == doc_type))
    if config is None:
        config = ApprovalSla(doc_type=doc_type)
        db.add(config)
    config.remind_hours = int(remind_hours)
    config.escalate_hours = int(escalate_hours) if escalate_hours is not None else None
    config.enabled = 1 if enabled else 0
    config.updated_by = username
    config.updated_at = datetime.now()
    db.commit()
    db.refresh(config)
    return {
        "doc_type": config.doc_type,
        "label": approval_flow.DOC_LABELS.get(config.doc_type, config.doc_type),
        "remind_hours": int(config.remind_hours),
        "escalate_hours": int(config.escalate_hours) if config.escalate_hours else None,
        "enabled": bool(config.enabled),
        "configured": True,
        "updated_by": config.updated_by,
        "updated_at": config.updated_at,
    }


def _config_map(db: Session) -> dict[str, ApprovalSla]:
    return {row.doc_type: row for row in db.scalars(select(ApprovalSla)).all()}


def pending_since(db: Session, doc_type: str, doc_id: int, fallback: datetime | None) -> datetime:
    """待审批起点：取最后一次 submit 日志时间，没有日志时退回单据时间。"""
    since = db.scalar(
        select(func.max(ApprovalLog.created_at)).where(
            ApprovalLog.order_type == doc_type,
            ApprovalLog.order_id == doc_id,
            ApprovalLog.action == "submit",
        )
    )
    return since or fallback or datetime.now()


def overdue_hours(db: Session, doc_type: str, doc_id: int, fallback: datetime | None) -> float:
    since = pending_since(db, doc_type, doc_id, fallback)
    return max((datetime.now() - since).total_seconds() / 3600, 0.0)


def _last_action_time(db: Session, doc_type: str, doc_id: int, action: str) -> datetime | None:
    return db.scalar(
        select(func.max(ApprovalLog.created_at)).where(
            ApprovalLog.order_type == doc_type,
            ApprovalLog.order_id == doc_id,
            ApprovalLog.action == action,
        )
    )


def _notify(db: Session, title: str, lines: list[str], template: str) -> None:
    """超时提醒走飞书（已配置时）；未配置就只留审批日志，不阻断扫描。"""
    try:
        from . import feishu, notify_config

        config = notify_config.effective(db)
        if not notify_config.ready(config):
            return
        card = feishu.build_card(title, template, lines, footer="AI-ERP 审批超时巡检")
        feishu.send_card(card, config["webhook_url"], config["secret"])
    except Exception:  # noqa: BLE001 通知失败不能影响其他单据的巡检
        import logging

        logging.getLogger(__name__).exception("审批超时通知发送失败")


def scan(db: Session, force: bool = False) -> dict:
    """扫描待审批单据：超过提醒小时先提醒一次，超过升级小时再升级一次。

    force=True 时忽略「本动作已处理过」的判断（手动巡检用）。
    """
    configs = _config_map(db)
    pending = approvals.pending_orders(db)
    reminded = 0
    escalated = 0
    skipped = 0
    for row in pending:
        doc_type = row["order_type"]
        config = configs.get(doc_type)
        if config is None or not config.enabled:
            skipped += 1
            continue
        remind_hours = max(int(config.remind_hours or DEFAULT_REMIND_HOURS), 1)
        escalate_hours = int(config.escalate_hours) if config.escalate_hours else None
        since = pending_since(db, doc_type, row["order_id"], row.get("order_date"))
        age = (datetime.now() - since).total_seconds() / 3600
        level = int(row.get("current_level") or 1)

        if escalate_hours and age >= escalate_hours:
            if not force and _last_action_time(db, doc_type, row["order_id"], "escalate") is not None:
                skipped += 1
                continue
            escalated += 1
            action = "escalate"
            comment = f"超时 {age:.1f} 小时未审批，已升级至上级"
        elif age >= remind_hours:
            if not force and _last_action_time(db, doc_type, row["order_id"], "remind") is not None:
                skipped += 1
                continue
            reminded += 1
            action = "remind"
            comment = f"超时 {age:.1f} 小时未审批，已提醒审批人"
        else:
            skipped += 1
            continue

        db.add(
            ApprovalLog(
                order_type=doc_type,
                order_id=row["order_id"],
                action=action,
                level=level,
                operator="系统巡检",
                principal="",
                role="system",
                comment=comment[:300],
            )
        )
        _notify(
            db,
            f"审批超时：{row['order_no']}",
            [
                f"单据：{approval_flow.DOC_LABELS.get(doc_type, doc_type)} {row['order_no']}",
                f"往来：{row.get('partner_name') or '—'}　金额：{row.get('total_amount') or 0:,.2f}",
                f"已等待：{age:.1f} 小时（提醒 {remind_hours} 小时"
                + (f"，升级 {escalate_hours} 小时" if escalate_hours else "")
                + f"）　当前第 {level} 级",
                comment,
            ],
            "red" if action == "escalate" else "orange",
        )

        # 站内消息收口：超时提醒给审批人，升级给管理员；与飞书并存
        from . import inbox

        target_users = (
            inbox.users_by_role(db, "admin")
            if action == "escalate"
            else inbox.users_by_permission(db, approval_flow.DEFAULT_PERMISSIONS.get(doc_type, ""))
        )
        inbox.push(
            db,
            target_users,
            category="sla",
            title=f"审批超时：{row['order_no']}",
            body=(
                f"{approval_flow.DOC_LABELS.get(doc_type, doc_type)} {row['order_no']} "
                f"已等待 {age:.1f} 小时，{comment}。"
            ),
            ref_type=doc_type,
            ref_id=row["order_id"],
        )
    db.commit()
    return {
        "checked": len(pending),
        "reminded": reminded,
        "escalated": escalated,
        "skipped": skipped,
        "at": datetime.now().isoformat(timespec="seconds"),
    }


def overdue_rows(db: Session, rows: list[dict]) -> list[dict]:
    """给待办列表补上「已等待多久 / 是否超时 / 是否升级」，供前端打标签。"""
    configs = _config_map(db)
    for row in rows:
        config = configs.get(row["order_type"])
        remind_hours = int(config.remind_hours or DEFAULT_REMIND_HOURS) if config and config.enabled else 0
        escalate_hours = (
            int(config.escalate_hours)
            if config and config.enabled and config.escalate_hours
            else None
        )
        age = overdue_hours(db, row["order_type"], row["order_id"], row.get("order_date"))
        row["waiting_hours"] = round(age, 1)
        row["overdue"] = bool(remind_hours and age >= remind_hours)
        row["escalated"] = bool(escalate_hours and age >= escalate_hours)
        row["sla_hours"] = remind_hours
        row["escalate_hours"] = escalate_hours
    return rows