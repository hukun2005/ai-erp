"""列表筛选与排序的共用解析。

销售单、采购单等台账页面的筛选口径必须一致：日期按「自然日闭区间」比较，
金额按区间比较，排序只允许白名单字段——避免前端任意传字段导致全表扫描。
"""
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import Select

# 排序白名单：前端只能选这些键，SQL 由后端拼装，不接受裸字段名
SORT_OPTIONS = {
    "created_desc": ("创建时间（新→旧）", lambda model: model.id.desc()),
    "created_asc": ("创建时间（旧→新）", lambda model: model.id.asc()),
    "date_desc": ("单据日期（新→旧）", lambda model: model.order_date.desc()),
    "date_asc": ("单据日期（旧→新）", lambda model: model.order_date.asc()),
    "amount_desc": ("金额（高→低）", lambda model: model.total_amount.desc()),
    "amount_asc": ("金额（低→高）", lambda model: model.total_amount.asc()),
}
DEFAULT_SORT = "created_desc"


def parse_day(value: str, label: str) -> datetime | None:
    """把 YYYY-MM-DD（也接受带时间的 ISO）解析成当天 00:00。"""
    text = (value or "").strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            return datetime.strptime(text, "%Y-%m-%d")
        return datetime.fromisoformat(text).replace(hour=0, minute=0, second=0, microsecond=0)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=f"{label}格式应为 YYYY-MM-DD") from error


def resolve_sort(sort: str) -> str:
    key = (sort or "").strip() or DEFAULT_SORT
    if key not in SORT_OPTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的排序方式「{key}」，可选：{'、'.join(SORT_OPTIONS)}",
        )
    return key


def apply_common_filters(
    stmt: Select,
    model,
    *,
    start_date: str = "",
    end_date: str = "",
    amount_min: float | None = None,
    amount_max: float | None = None,
    created_by: str = "",
) -> Select:
    """按单据日期区间、金额区间与制单人过滤；所有条件可叠加。"""
    start = parse_day(start_date, "开始日期")
    end = parse_day(end_date, "结束日期")
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    if start:
        stmt = stmt.where(model.order_date >= start)
    if end:
        # 闭区间：结束日当天 23:59:59 也要命中
        stmt = stmt.where(model.order_date < end + timedelta(days=1))
    if amount_min is not None:
        if amount_min < 0:
            raise HTTPException(status_code=400, detail="最小金额不能为负数")
        stmt = stmt.where(model.total_amount >= amount_min)
    if amount_max is not None:
        if amount_max < 0:
            raise HTTPException(status_code=400, detail="最大金额不能为负数")
        stmt = stmt.where(model.total_amount <= amount_max)
    if amount_min is not None and amount_max is not None and amount_min > amount_max:
        raise HTTPException(status_code=400, detail="最小金额不能大于最大金额")
    if created_by:
        stmt = stmt.where(model.created_by == created_by.strip())
    return stmt


def apply_sort(stmt: Select, model, sort: str) -> Select:
    _, builder = SORT_OPTIONS[resolve_sort(sort)]
    return stmt.order_by(builder(model), model.id.desc())


def options_payload() -> list[dict]:
    """给前端下拉框用的排序选项。"""
    return [{"value": key, "label": label} for key, (label, _) in SORT_OPTIONS.items()]
