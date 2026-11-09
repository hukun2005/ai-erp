"""飞书接入配置的解析：优先用页面保存的数据库配置，其次回退到 .env。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import FeishuConfig
from . import feishu


def saved(db: Session) -> FeishuConfig | None:
    return db.scalars(select(FeishuConfig).order_by(FeishuConfig.id)).first()


def effective(db: Session) -> dict:
    row = saved(db)
    if row and row.webhook_url:
        return {
            "webhook_url": row.webhook_url,
            "secret": row.secret or "",
            "enabled": bool(row.is_active),
            "source": "database",
            "row": row,
        }
    if settings.FEISHU_WEBHOOK_URL:
        return {
            "webhook_url": settings.FEISHU_WEBHOOK_URL,
            "secret": settings.FEISHU_SECRET,
            "enabled": True,
            "source": "env",
            "row": row,
        }
    return {"webhook_url": "", "secret": "", "enabled": False, "source": "none", "row": row}


def ready(cfg: dict) -> bool:
    return bool(cfg["webhook_url"]) and bool(cfg["enabled"])


def payload(db: Session) -> dict:
    cfg = effective(db)
    row: FeishuConfig | None = cfg["row"]
    return {
        "configured": ready(cfg),
        "enabled": cfg["enabled"],
        "source": cfg["source"],
        "webhook_url": cfg["webhook_url"],
        "webhook": feishu.masked(cfg["webhook_url"]),
        "secret_configured": bool(cfg["secret"]),
        "updated_by": row.updated_by if row else "",
        "updated_at": row.updated_at.isoformat(timespec="seconds") if row and row.updated_at else "",
    }
