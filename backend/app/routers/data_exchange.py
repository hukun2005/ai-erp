"""期初导入 / 期末导出接口。

- 期初：`templates/{dataset}` 下模板，`opening-import/{dataset}` 上传；
- 期末：`export` 按期间生成一个多 Sheet Excel；
- 概览：数量与期初余额，供页面对账；
- 日志：模板下载、导入成功/失败、导出成功/失败都写入审计日志。
"""
from datetime import datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import get_current_user, require_perm
from ..models import AuditLog, User
from ..permissions import has_permission
from ..services import exchange
from ..services.importer import XLSX_MEDIA_TYPE

router = APIRouter(prefix="/api/data-exchange", tags=["数据交换"])

DATASETS = {
    "products": {"label": "商品", "view": "product:view", "manage": "product:manage", "importer": exchange.import_products},
    "customers": {"label": "客户", "view": "customer:view", "manage": "customer:manage", "importer": exchange.import_customers},
    "suppliers": {"label": "供应商", "view": "supplier:view", "manage": "supplier:manage", "importer": exchange.import_suppliers},
}

MAX_PERIOD_DAYS = 400  # 一个季度绰绰有余，同时防止误拉好几年数据

# 本模块的动作集合：审计日志筛选、页面最近记录都以此为准
LOG_ACTIONS = ("下载期初模板", "期初导入商品", "期初导入客户", "期初导入供应商", "期末导出")


def _dataset(key: str) -> dict:
    if key not in DATASETS:
        raise HTTPException(status_code=404, detail="不支持的数据集")
    return DATASETS[key]


def _require(user: User, permission: str) -> None:
    if not has_permission(user.role, permission):
        raise HTTPException(status_code=403, detail=f"当前角色无此操作权限（{permission}）")


def _log_failed(user: User, request: Request, action: str, detail: str, status_code: int) -> None:
    record(
        username=user.username,
        role=user.role,
        action=action,
        resource="数据交换",
        detail=detail,
        result="failed",
        request=request,
        status_code=status_code,
    )


@router.get("/overview", summary="数据交换概览", dependencies=[Depends(require_perm("finance:view"))])
def data_overview(db: Session = Depends(get_db)):
    return exchange.overview(db)


@router.get("/logs", summary="导入导出最近操作记录", dependencies=[Depends(require_perm("finance:view"))])
def recent_logs(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.action.in_(LOG_ACTIONS))
        .order_by(AuditLog.id.desc())
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "username": row.username,
                "action": row.action,
                "result": row.result,
                "detail": row.detail,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    }


@router.get("/templates/{dataset}", summary="下载期初导入模板")
def download_template(dataset: str, request: Request, user: User = Depends(get_current_user)):
    meta = _dataset(dataset)
    _require(user, meta["view"])
    content = exchange.build_template_bytes(dataset)
    filename = quote(f"{meta['label']}期初导入模板.xlsx")
    record(
        username=user.username,
        role=user.role,
        action="下载期初模板",
        resource="数据交换",
        detail=f"{meta['label']}期初导入模板.xlsx",
        request=request,
    )
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.post("/opening-import/{dataset}", summary="导入期初数据（Excel）")
def opening_import(
    dataset: str,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    meta = _dataset(dataset)
    _require(user, meta["manage"])
    action = f"期初导入{meta['label']}"
    try:
        result = meta["importer"](db, file.file.read(), user)
    except ValueError as exc:
        db.rollback()
        _log_failed(user, request, action, f"导入失败：{exc}", 400)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 导入异常兜底，避免日志缺失
        db.rollback()
        _log_failed(user, request, action, f"导入异常：{exc}", 500)
        raise
    record(
        username=user.username,
        role=user.role,
        action=action,
        resource="数据交换",
        detail=(
            f"文件 {file.filename}；新增 {result['created']} 条，"
            f"更新期初余额 {result['updated']} 条，跳过 {result['skipped']} 条"
        ),
        request=request,
    )
    return result


@router.post("/opening-import/{dataset}/preview", summary="导入前校验（不入库）")
def preview_import(
    dataset: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    meta = _dataset(dataset)
    _require(user, meta["manage"])
    try:
        return meta["importer"](db, file.file.read(), user, dry_run=True)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:  # noqa: BLE001 校验异常兜底
        db.rollback()
        raise


@router.post("/opening-import/{dataset}/error-report", summary="导出校验错误行（xlsx）")
def error_report(
    dataset: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    meta = _dataset(dataset)
    _require(user, meta["manage"])
    try:
        result = meta["importer"](db, file.file.read(), user, dry_run=True)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:  # noqa: BLE001 校验异常兜底
        db.rollback()
        raise
    content = exchange.build_error_report_bytes(dataset, result["errors"])
    filename = quote(f"{meta['label']}导入错误报告.xlsx")
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.get("/export", summary="期末导出（多 Sheet Excel）", dependencies=[Depends(require_perm("finance:view"))])
def export_data(
    request: Request,
    start: str = Query(..., description="期间开始 yyyy-mm-dd"),
    end: str = Query(..., description="期间结束 yyyy-mm-dd（含当天）"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
    except ValueError as exc:
        _log_failed(user, request, "期末导出", f"日期格式不合法：{start} ~ {end}", 400)
        raise HTTPException(status_code=400, detail="日期格式应为 yyyy-mm-dd") from exc
    if end_dt < start_dt:
        _log_failed(user, request, "期末导出", f"区间倒置：{start} ~ {end}", 400)
        raise HTTPException(status_code=400, detail="结束日期必须晚于开始日期")
    if (end_dt - start_dt).days > MAX_PERIOD_DAYS:
        _log_failed(user, request, "期末导出", f"区间超长：{start} ~ {end}", 400)
        raise HTTPException(status_code=400, detail=f"单次导出最长 {MAX_PERIOD_DAYS} 天，请分段导出")

    end_exclusive = end_dt + timedelta(days=1)
    try:
        path = exchange.build_export(db, start_dt, end_exclusive)
    except Exception as exc:  # noqa: BLE001 导出异常兜底，避免日志缺失
        _log_failed(user, request, "期末导出", f"导出异常：{exc}", 500)
        raise
    record(
        username=user.username,
        role=user.role,
        action="期末导出",
        resource="数据交换",
        detail=f"{start} ~ {end}",
        request=request,
    )
    filename = quote(f"数据导出_{start}_{end}.xlsx")
    return StreamingResponse(
        exchange.stream_export(path),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )

