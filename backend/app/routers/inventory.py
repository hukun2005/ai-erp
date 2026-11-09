"""库存管理：仓管员盘点、手工出入库与库存流水。"""
import io
import logging
import re
from datetime import date, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import require_perm
from ..models import Product, StockBalance, StockLog, StorageLocation, User, Warehouse
from ..schemas import StockAdjust, StockLogOut
from ..services import alerts, reservation, transfer
from ..services.importer import PRODUCT_TYPE_LABELS
from ..services.stock import apply_stock_change, deduct_stock

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/inventory", tags=["库存"])

MAX_EXPORT_ROWS = 10000  # 单次导出上限，避免大范围查询占满内存
VALID_CHANGE_TYPES = {"in", "out", "check"}
VALID_PRODUCT_TYPES = {"raw_material", "finished_good"}
MAX_KEYWORD_LENGTH = 50


def _normalize_product_type(product_type: str) -> str:
    """空字符串表示全部；非法值统一返回 400。"""
    value = (product_type or "").strip()
    if value and value not in VALID_PRODUCT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="物料类型不合法，可选：raw_material / finished_good",
        )
    return value


def _product_type_stats(db: Session, product_type: str) -> dict:
    product_count = db.scalar(
        select(func.count()).select_from(Product).where(Product.product_type == product_type)
    ) or 0
    total_qty = db.scalar(
        select(func.sum(Product.stock_qty)).where(Product.product_type == product_type)
    ) or 0
    value = db.scalar(
        select(func.sum(Product.stock_qty * Product.cost_price)).where(
            Product.product_type == product_type
        )
    ) or 0
    low = db.scalar(
        select(func.count()).select_from(Product)
        .where(Product.product_type == product_type)
        .where(Product.is_active == True)  # noqa: E712
        .where(Product.stock_qty <= Product.safety_stock)
    ) or 0
    zero = db.scalar(
        select(func.count()).select_from(Product)
        .where(Product.product_type == product_type)
        .where(Product.stock_qty <= 0)
    ) or 0
    return {
        "product_count": int(product_count),
        "total_qty": int(total_qty),
        "total_value": round(float(value), 2),
        "low_stock_count": int(low),
        "out_of_stock_count": int(zero),
    }


@router.get("/summary", summary="库存概览", dependencies=[Depends(require_perm("inventory:view"))])
def summary(db: Session = Depends(get_db)):
    total_qty = db.scalar(select(func.sum(Product.stock_qty))) or 0
    value = db.scalar(select(func.sum(Product.stock_qty * Product.cost_price))) or 0
    low = db.scalar(
        select(func.count()).select_from(Product)
        .where(Product.is_active == True)  # noqa: E712
        .where(Product.stock_qty <= Product.safety_stock)
    ) or 0
    zero = db.scalar(
        select(func.count()).select_from(Product).where(Product.stock_qty <= 0)
    ) or 0
    return {
        "total_qty": int(total_qty),
        "total_value": round(float(value), 2),
        "low_stock_count": int(low),
        "out_of_stock_count": int(zero),
        "product_count": db.scalar(select(func.count()).select_from(Product)) or 0,
        "raw_material": _product_type_stats(db, "raw_material"),
        "finished_good": _product_type_stats(db, "finished_good"),
        **transfer.stats(db),
    }


def _stock_stmt(keyword: str, only_low: bool, product_type: str = ""):
    """库存列表与导出复用的筛选条件，保证页面看到的范围与报表一致。"""
    stmt = select(Product)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(Product.sku.like(like), Product.name.like(like)))
    if only_low:
        stmt = stmt.where(Product.stock_qty <= Product.safety_stock)
    if product_type:
        stmt = stmt.where(Product.product_type == product_type)
    return stmt


@router.get("/stock", summary="库存列表", dependencies=[Depends(require_perm("inventory:view"))])
def stock_list(
    keyword: str = Query(""),
    only_low: bool = Query(False),
    product_type: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    product_type = _normalize_product_type(product_type)
    stmt = _stock_stmt(keyword, only_low, product_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by((Product.stock_qty - Product.safety_stock))
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    in_transit = transfer.in_transit_map(db)
    reserved = reservation.reserved_map(db, [p.id for p in rows])
    return {
        "total": total,
        "items": [
            {
                "id": p.id,
                "sku": p.sku,
                "name": p.name,
                "category": p.category,
                "product_type": p.product_type,
                "unit": p.unit,
                "stock_qty": p.stock_qty,
                # 可用量 = 现存 − 已确认订单占用，避免业务按「现存」承诺交期
                "reserved_qty": reserved.get(p.id, 0),
                "available_qty": int(p.stock_qty or 0) - reserved.get(p.id, 0),
                "in_transit_qty": in_transit.get(p.id, 0),
                "safety_stock": p.safety_stock,
                "cost_price": p.cost_price,
                "stock_value": round(p.stock_qty * p.cost_price, 2),
                "status": "缺货" if p.stock_qty <= 0 else ("预警" if p.stock_qty <= p.safety_stock else "正常"),
            }
            for p in rows
        ],
    }


@router.post("/adjust", summary="库存调整（出入库/盘点）", dependencies=[Depends(require_perm("inventory:adjust"))])
def adjust_stock(
    payload: StockAdjust,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("inventory:adjust")),
):
    product = db.get(Product, payload.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")

    if payload.change_type == "out":
        # 出库跨批次按先到期先出扣减，未指定批次也能正确扣
        before_total = int(product.stock_qty or 0)
        rows = deduct_stock(
            db,
            product,
            payload.quantity,
            reason=payload.reason,
            biz_type="手工出库",
            username=user.username,
        )
        result = {
            "product_id": product.id,
            "warehouse_id": payload.warehouse_id,
            "warehouse_name": rows[0]["warehouse_name"] if rows else "",
            "location_id": payload.location_id,
            "location_code": rows[0]["location_code"] if rows else "",
            "change_qty": -payload.quantity,
            "stock_before": before_total,
            "stock_after": int(product.stock_qty or 0),
            "batches": [row["batch_no"] for row in rows],
        }
    else:
        result = apply_stock_change(
            db,
            product,
            payload.change_type,
            payload.quantity,
            warehouse_id=payload.warehouse_id,
            location_id=payload.location_id,
            batch_no=getattr(payload, "batch_no", "") or "",
            expiry_date=getattr(payload, "expiry_date", None),
            reason=payload.reason,
            biz_type="手工出入库",
            username=user.username,
        )
    db.commit()
    change_names = {"in": "入库", "out": "出库", "check": "盘点"}
    record(
        username=user.username,
        role=user.role,
        action="库存调整",
        resource=f"商品 {product.sku} {product.name}",
        detail=(
            f"{change_names[payload.change_type]} {result['change_qty']:+d}，"
            f"{result['warehouse_name']}/{result['location_code']} 库存 "
            f"{result['stock_before']} → {result['stock_after']}；原因：{payload.reason or '未填写'}"
        ),
        request=request,
    )
    if payload.change_type in ("out", "check"):
        # 库存减少后检查自动预警（规则未启用或未接入飞书时静默跳过）
        result["auto_alert"] = alerts.check_and_notify(
            db, [product.id], user.username, scene=f"库存{change_names[payload.change_type]}"
        )
    return result


def _distribution_stmt(
    keyword: str, warehouse_id: int | None, db: Session, product_type: str = ""
):
    """库存分布查询条件（列表与导出共用），返回 (stmt, 仓库对象或 None)。"""
    keyword = (keyword or "").strip()
    if len(keyword) > MAX_KEYWORD_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"搜索关键字不能超过 {MAX_KEYWORD_LENGTH} 个字符"
        )
    stmt = (
        select(StockBalance)
        .join(Product, Product.id == StockBalance.product_id)
        .join(Warehouse, Warehouse.id == StockBalance.warehouse_id)
        .where(StockBalance.quantity > 0)
    )
    warehouse = None
    if warehouse_id is not None:
        if warehouse_id <= 0:
            raise HTTPException(status_code=400, detail="仓库编号不合法")
        warehouse = db.get(Warehouse, warehouse_id)
        if not warehouse:
            raise HTTPException(status_code=404, detail="仓库不存在")
        stmt = stmt.where(StockBalance.warehouse_id == warehouse_id)
    if product_type:
        stmt = stmt.where(Product.product_type == product_type)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(Product.sku.like(like), Product.name.like(like))
                          | Warehouse.name.like(like))
    return stmt, warehouse


@router.get("/distribution", summary="库存分布（按仓库/库位）", dependencies=[Depends(require_perm("inventory:view"))])
def stock_distribution(
    keyword: str = Query(""),
    warehouse_id: int | None = Query(None),
    product_type: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    product_type = _normalize_product_type(product_type)
    stmt, _ = _distribution_stmt(keyword, warehouse_id, db, product_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Product.sku, StockBalance.warehouse_id, StockBalance.location_id)
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "total": total,
        "items": [
            {
                "id": b.id,
                "product_id": b.product_id,
                "product_sku": b.product.sku if b.product else "",
                "product_name": b.product.name if b.product else "",
                "product_type": b.product.product_type if b.product else "",
                "unit": b.product.unit if b.product else "",
                "warehouse_id": b.warehouse_id,
                "warehouse_name": b.warehouse.name if b.warehouse else "",
                "location_id": b.location_id,
                "location_code": b.location.code if b.location else "",
                "location_name": b.location.name if b.location else "",
                "batch_no": b.batch_no,
                "expiry_date": b.expiry_date,
                "quantity": b.quantity,
                "updated_at": b.updated_at,
            }
            for b in rows
        ],
    }


@router.get("/expiring", summary="临期/过期批次", dependencies=[Depends(require_perm("inventory:view"))])
def expiring_batches(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """返回在 days 天内到期、以及已经过期但仍有库存的批次。"""
    from datetime import timedelta

    deadline = datetime.now() + timedelta(days=days)
    rows = db.execute(
        select(StockBalance, Product, Warehouse, StorageLocation)
        .join(Product, Product.id == StockBalance.product_id)
        .join(Warehouse, Warehouse.id == StockBalance.warehouse_id)
        .join(StorageLocation, StorageLocation.id == StockBalance.location_id)
        .where(StockBalance.quantity > 0)
        .where(StockBalance.expiry_date.is_not(None))
        .where(StockBalance.expiry_date <= deadline)
        .order_by(StockBalance.expiry_date)
    ).all()
    now = datetime.now()
    items = []
    for balance, product, warehouse, location in rows:
        left = (balance.expiry_date - now).days if balance.expiry_date else 0
        items.append(
            {
                "balance_id": balance.id,
                "product_id": product.id,
                "product_sku": product.sku,
                "product_name": product.name,
                "unit": product.unit,
                "batch_no": balance.batch_no,
                "expiry_date": balance.expiry_date,
                "days_left": left,
                "quantity": balance.quantity,
                "warehouse_name": warehouse.name,
                "location_code": location.code,
                "expired": left < 0,
            }
        )
    return {
        "total": len(items),
        "expired": sum(1 for row in items if row["expired"]),
        "days": days,
        "items": items,
    }


@router.get("/logs", summary="库存变动流水", dependencies=[Depends(require_perm("inventory:view"))])
def stock_logs(
    keyword: str = Query(""),
    product_id: int | None = Query(None),
    change_type: str = Query(""),
    warehouse_id: int | None = Query(None),
    location_id: int | None = Query(None),
    product_type: str = Query(""),
    start_date: str = Query(""),
    end_date: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    filters = _validate_filters(
        keyword, product_id, change_type, start_date, end_date, warehouse_id, location_id, db,
        product_type,
    )
    stmt = _log_stmt(filters)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(StockLog.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    names = _location_names(db)
    return {
        "total": total,
        "items": [_serialize_log(log, names) for log in rows],
    }


def _location_names(db: Session) -> dict:
    """仓库/库位名称字典：流水数量大但仓库库位总量小，一次加载即可。"""
    warehouses = {w.id: w.name for w in db.scalars(select(Warehouse)).all()}
    locations = {loc.id: loc.code for loc in db.scalars(select(StorageLocation)).all()}
    return {"warehouses": warehouses, "locations": locations}


def _serialize_log(log: StockLog, names: dict) -> StockLogOut:
    return StockLogOut(
        id=log.id,
        product_id=log.product_id,
        product_name=log.product.name if log.product else "",
        product_sku=log.product.sku if log.product else "",
        product_type=log.product.product_type if log.product else "",
        warehouse_id=log.warehouse_id,
        warehouse_name=names["warehouses"].get(log.warehouse_id, ""),
        location_id=log.location_id,
        location_code=names["locations"].get(log.location_id, ""),
        change_type=log.change_type,
        biz_type=log.biz_type,
        ref_no=log.ref_no,
        change_qty=log.change_qty,
        stock_before=log.stock_before,
        stock_after=log.stock_after,
        reason=log.reason,
        created_by=log.created_by,
        created_at=log.created_at,
    )


CHANGE_TYPE_NAMES = {"in": "入库", "out": "出库", "check": "盘点"}


def _parse_date(value: str, label: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{label}格式不正确，应为 YYYY-MM-DD")


def _validate_filters(
    keyword: str,
    product_id: int | None,
    change_type: str,
    start_date: str,
    end_date: str,
    warehouse_id: int | None,
    location_id: int | None,
    db: Session,
    product_type: str = "",
) -> dict:
    """校验并归一化查询条件，列表与 Excel 导出共用。"""
    keyword = (keyword or "").strip()
    if len(keyword) > MAX_KEYWORD_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"搜索关键字不能超过 {MAX_KEYWORD_LENGTH} 个字符"
        )
    change_type = (change_type or "").strip()
    if change_type and change_type not in VALID_CHANGE_TYPES:
        raise HTTPException(status_code=400, detail="不支持的流水类型，可选：in / out / check")
    product_type = _normalize_product_type(product_type)
    start = _parse_date(start_date, "开始日期")
    end = _parse_date(end_date, "结束日期")
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    if product_id is not None:
        if product_id <= 0:
            raise HTTPException(status_code=400, detail="商品编号不合法")
        if not db.get(Product, product_id):
            raise HTTPException(status_code=404, detail="商品不存在")
    warehouse = None
    if warehouse_id is not None:
        if warehouse_id <= 0:
            raise HTTPException(status_code=400, detail="仓库编号不合法")
        warehouse = db.get(Warehouse, warehouse_id)
        if not warehouse:
            raise HTTPException(status_code=404, detail="仓库不存在")
    if location_id is not None:
        if location_id <= 0:
            raise HTTPException(status_code=400, detail="库位编号不合法")
        location = db.get(StorageLocation, location_id)
        if not location:
            raise HTTPException(status_code=404, detail="库位不存在")
        if warehouse and location.warehouse_id != warehouse.id:
            raise HTTPException(status_code=400, detail="库位不属于所选仓库")
    return {
        "keyword": keyword,
        "product_id": product_id,
        "change_type": change_type,
        "start": start,
        "end": end,
        "warehouse_id": warehouse_id,
        "location_id": location_id,
        "product_type": product_type,
    }


def _log_stmt(filters: dict):
    """库存流水查询条件（已校验）。"""
    stmt = select(StockLog)
    if filters["product_id"]:
        stmt = stmt.where(StockLog.product_id == filters["product_id"])
    if filters["change_type"]:
        stmt = stmt.where(StockLog.change_type == filters["change_type"])
    if filters["warehouse_id"]:
        stmt = stmt.where(StockLog.warehouse_id == filters["warehouse_id"])
    if filters["location_id"]:
        stmt = stmt.where(StockLog.location_id == filters["location_id"])
    if filters["start"]:
        stmt = stmt.where(func.date(StockLog.created_at) >= filters["start"].isoformat())
    if filters["end"]:
        stmt = stmt.where(func.date(StockLog.created_at) <= filters["end"].isoformat())
    if filters["keyword"] or filters["product_type"]:
        stmt = stmt.join(Product, Product.id == StockLog.product_id)
    if filters["keyword"]:
        like = f"%{filters['keyword']}%"
        stmt = stmt.where(
            or_(
                Product.name.like(like),
                Product.sku.like(like),
                StockLog.reason.like(like),
                StockLog.ref_no.like(like),
            )
        )
    if filters["product_type"]:
        stmt = stmt.where(Product.product_type == filters["product_type"])
    return stmt


THIN = Side(style="thin", color="D1D5DB")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _style_header(ws, row: int, cols: int):
    for col in range(1, cols + 1):
        cell = ws.cell(row=row, column=col)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="6366F1")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER


def _style_body(ws, first_row: int, last_row: int, cols: int, text_cols: tuple[int, ...] = ()):
    for row in range(first_row, last_row + 1):
        for col in range(1, cols + 1):
            cell = ws.cell(row=row, column=col)
            cell.border = BORDER
            cell.alignment = Alignment(
                horizontal="left" if col in text_cols else "center", vertical="center"
            )


def _build_report(rows: list[StockLog], names: dict, period: str, username: str) -> bytes:
    """生成 Excel 报表字节流；样式与内容集中在此，异常由调用方统一处理。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "出入库明细"
    headers = [
        "序号", "时间", "商品SKU", "商品名称", "物料类型", "仓库", "库位", "类型",
        "业务类型", "关联单号", "变动数量", "变动前库存", "变动后库存", "原因", "操作人",
    ]
    widths = [6, 20, 14, 26, 10, 14, 10, 10, 14, 16, 11, 12, 12, 26, 12]

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    title = ws.cell(row=1, column=1)
    title.value = "库存出入库报表"
    title.font = Font(size=16, bold=True)
    title.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))
    info = ws.cell(row=2, column=1)
    info.value = (
        f"统计期间：{period}    记录条数：{len(rows)}    生成时间：{datetime.now():%Y-%m-%d %H:%M}    生成人：{username}"
    )
    info.font = Font(size=10, color="6B7280")
    info.alignment = Alignment(horizontal="center")
    ws.row_dimensions[2].height = 20

    ws.append(headers)
    _style_header(ws, 3, len(headers))
    ws.row_dimensions[3].height = 22

    for index, log in enumerate(rows, start=1):
        ws.append(
            [
                index,
                log.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                log.product.sku if log.product else "",
                log.product.name if log.product else "",
                PRODUCT_TYPE_LABELS.get(log.product.product_type, "") if log.product else "",
                names["warehouses"].get(log.warehouse_id, ""),
                names["locations"].get(log.location_id, ""),
                CHANGE_TYPE_NAMES.get(log.change_type, log.change_type),
                log.biz_type or "",
                log.ref_no or "",
                log.change_qty,
                log.stock_before,
                log.stock_after,
                log.reason or "",
                log.created_by,
            ]
        )
    if rows:
        _style_body(ws, 4, 3 + len(rows), len(headers), text_cols=(3, 4, 6, 7, 9, 10, 14))
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.freeze_panes = "A4"

    # 第二个工作表：按商品汇总出入库数量
    summary_ws = wb.create_sheet("商品汇总")
    summary_headers = ["商品SKU", "商品名称", "物料类型", "入库数量", "出库数量", "盘点次数", "净变动", "当前库存"]
    summary_ws.append(summary_headers)
    _style_header(summary_ws, 1, len(summary_headers))
    for idx, width in enumerate([14, 30, 10, 12, 12, 12, 12, 12], start=1):
        summary_ws.column_dimensions[get_column_letter(idx)].width = width

    stats: dict[int, dict] = {}
    for log in rows:
        if not log.product:
            continue
        item = stats.setdefault(
            log.product_id,
            {
                "product": log.product,
                "in": 0,
                "out": 0,
                "check": 0,
                "net": 0,
            },
        )
        if log.change_type == "in":
            item["in"] += log.change_qty
        elif log.change_type == "out":
            item["out"] += abs(log.change_qty)
        else:
            item["check"] += 1
        item["net"] += log.change_qty

    for item in stats.values():
        product = item["product"]
        summary_ws.append(
            [
                product.sku,
                product.name,
                PRODUCT_TYPE_LABELS.get(product.product_type, ""),
                item["in"],
                item["out"],
                item["check"],
                item["net"],
                product.stock_qty,
            ]
        )
    if stats:
        _style_body(summary_ws, 2, 1 + len(stats), len(summary_headers), text_cols=(1, 2, 3))
    summary_ws.freeze_panes = "A2"

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _build_distribution_report(rows: list[StockBalance], scope: str, username: str) -> bytes:
    """生成库存分布 Excel（可按仓库筛选），样式与出入库报表保持一致。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "库存分布"
    headers = ["序号", "商品SKU", "商品名称", "物料类型", "单位", "仓库", "库位", "库位说明", "库存数量", "更新时间"]
    widths = [6, 14, 26, 10, 8, 16, 12, 18, 11, 20]
    total_qty = sum(b.quantity for b in rows)

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    title = ws.cell(row=1, column=1)
    title.value = "库存分布报表"
    title.font = Font(size=16, bold=True)
    title.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))
    info = ws.cell(row=2, column=1)
    info.value = (
        f"仓库范围：{scope}    记录条数：{len(rows)}    合计库存：{total_qty}    "
        f"生成时间：{datetime.now():%Y-%m-%d %H:%M}    生成人：{username}"
    )
    info.font = Font(size=10, color="6B7280")
    info.alignment = Alignment(horizontal="center")
    ws.row_dimensions[2].height = 20

    ws.append(headers)
    _style_header(ws, 3, len(headers))
    ws.row_dimensions[3].height = 22

    for index, balance in enumerate(rows, start=1):
        ws.append(
            [
                index,
                balance.product.sku if balance.product else "",
                balance.product.name if balance.product else "",
                PRODUCT_TYPE_LABELS.get(balance.product.product_type, "")
                if balance.product else "",
                balance.product.unit if balance.product else "",
                balance.warehouse.name if balance.warehouse else "",
                balance.location.code if balance.location else "",
                balance.location.name if balance.location else "",
                balance.quantity,
                balance.updated_at.strftime("%Y-%m-%d %H:%M:%S") if balance.updated_at else "",
            ]
        )
    if rows:
        _style_body(ws, 4, 3 + len(rows), len(headers), text_cols=(2, 3, 5, 6, 7, 8))
        ws.append(["", "合计", "", "", "", "", "", "", total_qty, ""])
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=4 + len(rows), column=col)
            cell.font = Font(bold=True)
            cell.border = BORDER
            cell.alignment = Alignment(
                horizontal="left" if col == 2 else "center", vertical="center"
            )
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.freeze_panes = "A4"

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _safe_filename_tag(value: str) -> str:
    """文件名片段：去掉路径分隔符与非法字符，避免下载文件名异常。"""
    tag = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", (value or "").strip())
    return tag[:30] or "全部仓库"


@router.get("/distribution/export", summary="导出库存分布 Excel 报表（可按仓库筛选）")
def export_distribution(
    request: Request,
    keyword: str = Query(""),
    warehouse_id: int | None = Query(None),
    product_type: str = Query(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("inventory:view")),
):
    product_type = _normalize_product_type(product_type)
    try:
        stmt, warehouse = _distribution_stmt(keyword, warehouse_id, db, product_type)
        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        if total > MAX_EXPORT_ROWS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"导出记录 {total} 条，超过单次上限 {MAX_EXPORT_ROWS} 条，"
                    "请选择具体仓库或缩小搜索关键字"
                ),
            )
        if total == 0:
            raise HTTPException(
                status_code=400, detail="当前筛选条件下没有可导出的库存分布记录，请调整仓库或关键字"
            )
        rows = db.scalars(
            stmt.order_by(Product.sku, StockBalance.warehouse_id, StockBalance.location_id)
        ).all()
    except HTTPException:
        raise
    except SQLAlchemyError:
        logger.exception(
            "库存分布查询失败：用户=%s，仓库=%s，关键字=%s", user.username, warehouse_id, keyword
        )
        record(
            username=user.username,
            role=user.role,
            action="导出库存分布",
            resource="库存分布报表",
            detail=f"仓库：{warehouse_id or '全部'}；关键字：{keyword or '未填写'}；查询失败",
            result="failed",
            request=request,
            status_code=500,
        )
        raise HTTPException(status_code=500, detail="查询库存分布失败，请稍后重试")

    if len(rows) != total:  # 统计与实际结果不一致时提示重试，避免导出不完整数据
        logger.warning("库存分布条数不一致：统计=%s，实际=%s", total, len(rows))
        raise HTTPException(status_code=500, detail="库存数据已变化，请刷新后重新导出")

    scope = f"{warehouse.code} {warehouse.name}" if warehouse else "全部仓库"
    if product_type:
        scope = f"{scope} · {PRODUCT_TYPE_LABELS.get(product_type, product_type)}"
    scope_tag = _safe_filename_tag(warehouse.code if warehouse else "全部仓库")
    if product_type:
        scope_tag = _safe_filename_tag(
            f"{scope_tag}{PRODUCT_TYPE_LABELS.get(product_type, product_type)}"
        )
    filename = f"库存分布报表_{scope_tag}_{datetime.now():%Y%m%d_%H%M}.xlsx"
    try:
        content = _build_distribution_report(rows, scope, user.username)
    except Exception:  # noqa: BLE001
        logger.exception(
            "库存分布报表生成失败：用户=%s，仓库=%s，条数=%s", user.username, scope, len(rows)
        )
        record(
            username=user.username,
            role=user.role,
            action="导出库存分布",
            resource=f"库存分布报表（{len(rows)} 条）",
            detail=f"仓库范围：{scope}；报表生成失败",
            result="failed",
            request=request,
            status_code=500,
        )
        raise HTTPException(status_code=500, detail="报表生成失败，请稍后重试")

    if not content:
        raise HTTPException(status_code=500, detail="报表生成失败，文件内容为空")

    record(
        username=user.username,
        role=user.role,
        action="导出库存分布",
        resource=f"库存分布报表（{len(rows)} 条）",
        detail=f"仓库范围：{scope}；关键字：{keyword or '未填写'}",
        request=request,
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Content-Length": str(len(content)),
        },
    )


@router.get("/logs/export", summary="导出库存出入库 Excel 报表")
def export_stock_logs(
    request: Request,
    keyword: str = Query(""),
    product_id: int | None = Query(None),
    change_type: str = Query(""),
    warehouse_id: int | None = Query(None),
    location_id: int | None = Query(None),
    product_type: str = Query(""),
    start_date: str = Query(""),
    end_date: str = Query(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("inventory:view")),
):
    try:
        filters = _validate_filters(
            keyword, product_id, change_type, start_date, end_date, warehouse_id, location_id, db,
            product_type,
        )
        stmt = _log_stmt(filters)
        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        if total > MAX_EXPORT_ROWS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"导出记录 {total} 条，超过单次上限 {MAX_EXPORT_ROWS} 条，"
                    "请缩小日期范围或增加商品/类型筛选"
                ),
            )
        if total == 0:
            raise HTTPException(
                status_code=400, detail="当前筛选条件下没有可导出的流水记录，请调整筛选条件"
            )
        rows = db.scalars(stmt.order_by(StockLog.created_at, StockLog.id)).all()
        names = _location_names(db)
    except HTTPException:
        raise
    except SQLAlchemyError:
        logger.exception(
            "库存流水查询失败：用户=%s，期间=%s~%s，仓库=%s",
            user.username,
            start_date,
            end_date,
            warehouse_id,
        )
        record(
            username=user.username,
            role=user.role,
            action="导出库存报表",
            resource="库存出入库报表",
            detail=f"期间：{start_date or '不限'}~{end_date or '不限'}；查询失败",
            result="failed",
            request=request,
            status_code=500,
        )
        raise HTTPException(status_code=500, detail="查询库存流水失败，请稍后重试")

    if len(rows) != total:  # 统计与实际结果不一致时提示重试，避免导出不完整数据
        logger.warning("库存流水条数不一致：统计=%s，实际=%s", total, len(rows))
        raise HTTPException(status_code=500, detail="库存流水已变化，请刷新后重新导出")

    start, end = filters["start"], filters["end"]

    period = f"{start or '不限'} ~ {end or '不限'}"
    scope_tag = "全部仓库"
    if filters["warehouse_id"]:
        warehouse = db.get(Warehouse, filters["warehouse_id"])
        if warehouse:
            scope_tag = _safe_filename_tag(warehouse.code)
    filename = f"库存出入库报表_{scope_tag}_{datetime.now():%Y%m%d_%H%M}.xlsx"
    try:
        content = _build_report(rows, names, period, user.username)
    except Exception:  # noqa: BLE001
        logger.exception(
            "库存报表生成失败：用户=%s，期间=%s，条数=%s",
            user.username,
            period,
            len(rows),
        )
        record(
            username=user.username,
            role=user.role,
            action="导出库存报表",
            resource=f"库存出入库报表（{len(rows)} 条）",
            detail=f"统计期间：{period}；报表生成失败",
            result="failed",
            request=request,
            status_code=500,
        )
        raise HTTPException(status_code=500, detail="报表生成失败，请稍后重试")

    if not content:
        raise HTTPException(status_code=500, detail="报表生成失败，文件内容为空")

    record(
        username=user.username,
        role=user.role,
        action="导出库存报表",
        resource=f"库存出入库报表（{len(rows)} 条）",
        detail=f"统计期间：{period}；类型：{CHANGE_TYPE_NAMES.get(filters['change_type'], '全部')}；仓库：{scope_tag}",
        request=request,
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Content-Length": str(len(content)),
        },
    )


def _build_stock_report(rows: list[Product], in_transit: dict, scope: str, username: str) -> bytes:
    """生成当前库存报表（含在途、安全库存与库存金额），样式与其它库存报表一致。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "库存报表"
    headers = [
        "序号", "商品SKU", "商品名称", "分类", "物料类型", "单位", "当前库存",
        "在途数量", "安全库存", "成本价", "库存金额", "库存状态",
    ]
    widths = [6, 14, 26, 12, 10, 8, 11, 11, 11, 11, 14, 10]
    total_qty = sum(p.stock_qty for p in rows)
    total_transit = sum(in_transit.get(p.id, 0) for p in rows)
    total_value = sum(p.stock_qty * p.cost_price for p in rows)

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    title = ws.cell(row=1, column=1)
    title.value = "库存报表"
    title.font = Font(size=16, bold=True)
    title.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))
    info = ws.cell(row=2, column=1)
    info.value = (
        f"统计范围：{scope}    商品数：{len(rows)}    库存总量：{total_qty}    "
        f"生成时间：{datetime.now():%Y-%m-%d %H:%M}    生成人：{username}"
    )
    info.font = Font(size=10, color="6B7280")
    info.alignment = Alignment(horizontal="center")
    ws.row_dimensions[2].height = 20

    ws.append(headers)
    _style_header(ws, 3, len(headers))
    ws.row_dimensions[3].height = 22

    for index, product in enumerate(rows, start=1):
        status = (
            "缺货" if product.stock_qty <= 0
            else ("预警" if product.stock_qty <= product.safety_stock else "正常")
        )
        ws.append(
            [
                index,
                product.sku,
                product.name,
                product.category or "",
                PRODUCT_TYPE_LABELS.get(product.product_type, ""),
                product.unit,
                product.stock_qty,
                in_transit.get(product.id, 0),
                product.safety_stock,
                product.cost_price,
                round(product.stock_qty * product.cost_price, 2),
                status,
            ]
        )
    if rows:
        _style_body(ws, 4, 3 + len(rows), len(headers), text_cols=(2, 3, 4))
        ws.append(
            ["", "合计", "", "", "", "", total_qty, total_transit, "", "", round(total_value, 2), ""]
        )
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=4 + len(rows), column=col)
            cell.font = Font(bold=True)
            cell.border = BORDER
            cell.alignment = Alignment(
                horizontal="left" if col == 2 else "center", vertical="center"
            )
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.freeze_panes = "A4"

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


@router.get("/stock/export", summary="导出库存列表 Excel 报表")
def export_stock_list(
    request: Request,
    keyword: str = Query(""),
    only_low: bool = Query(False),
    product_type: str = Query(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("inventory:view")),
):
    keyword = keyword.strip()
    if len(keyword) > MAX_KEYWORD_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"搜索关键字不能超过 {MAX_KEYWORD_LENGTH} 个字符"
        )
    product_type = _normalize_product_type(product_type)
    try:
        stmt = _stock_stmt(keyword, only_low, product_type)
        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        if total > MAX_EXPORT_ROWS:
            raise HTTPException(
                status_code=400,
                detail=f"导出记录 {total} 条，超过单次上限 {MAX_EXPORT_ROWS} 条，请缩小搜索范围",
            )
        if total == 0:
            raise HTTPException(
                status_code=400,
                detail="当前筛选条件下没有可导出的库存记录，请调整关键字或关闭「只看预警商品」",
            )
        rows = db.scalars(stmt.order_by(Product.sku)).all()
        in_transit = transfer.in_transit_map(db)
    except HTTPException:
        raise
    except SQLAlchemyError:
        logger.exception(
            "库存列表查询失败：用户=%s，关键字=%s，仅预警=%s", user.username, keyword, only_low
        )
        record(
            username=user.username,
            role=user.role,
            action="导出库存列表",
            resource="库存报表",
            detail=f"关键字：{keyword or '未填写'}；仅预警：{only_low}；查询失败",
            result="failed",
            request=request,
            status_code=500,
        )
        raise HTTPException(status_code=500, detail="查询库存列表失败，请稍后重试")

    if len(rows) != total:  # 统计与实际结果不一致时提示重试，避免导出不完整数据
        logger.warning("库存列表条数不一致：统计=%s，实际=%s", total, len(rows))
        raise HTTPException(status_code=500, detail="库存数据已变化，请刷新后重新导出")

    scope = PRODUCT_TYPE_LABELS.get(product_type, "全部商品")
    if only_low:
        scope += "（仅预警商品）"
    scope_tag = _safe_filename_tag(
        f"{PRODUCT_TYPE_LABELS.get(product_type, '全部商品')}{'预警' if only_low else ''}"
    )
    filename = f"库存报表_{scope_tag}_{datetime.now():%Y%m%d_%H%M}.xlsx"
    try:
        content = _build_stock_report(rows, in_transit, scope, user.username)
    except Exception:  # noqa: BLE001
        logger.exception(
            "库存报表生成失败：用户=%s，范围=%s，条数=%s", user.username, scope, len(rows)
        )
        record(
            username=user.username,
            role=user.role,
            action="导出库存列表",
            resource=f"库存报表（{len(rows)} 条）",
            detail=f"范围：{scope}；报表生成失败",
            result="failed",
            request=request,
            status_code=500,
        )
        raise HTTPException(status_code=500, detail="报表生成失败，请稍后重试")

    if not content:
        raise HTTPException(status_code=500, detail="报表生成失败，文件内容为空")

    record(
        username=user.username,
        role=user.role,
        action="导出库存列表",
        resource=f"库存报表（{len(rows)} 条）",
        detail=f"范围：{scope}；关键字：{keyword or '未填写'}",
        request=request,
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Content-Length": str(len(content)),
        },
    )

