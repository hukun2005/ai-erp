"""期初导入与期末导出：面向系统上线与季度结账的一条数据通道。

两条边界：
1. 期初只导入主数据与余额，**不伪造历史单据**；期初应收/应付单独落
   `opening_balances`，由应收应付账龄与对账单期初读取；
2. 期末导出按期间过滤，一个 Excel 多 Sheet 交付，行列顺序固定，方便会计归档。
"""
import os
import tempfile
from datetime import date, datetime

from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from ..models import (
    Customer,
    OpeningBalance,
    Payment,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseReturn,
    SalesOrder,
    SalesOrderItem,
    SalesReturn,
    StockBalance,
    StockLog,
    StorageLocation,
    Supplier,
    SupplierPayment,
    User,
    Warehouse,
)
from ..scoping import own_scope
from . import payables, receivables
from .importer import (
    PRODUCT_TYPE_LABELS,
    build_error_report,
    build_template,
    parse_int,
    parse_number,
    parse_product_type,
    parse_workbook,
)
from .stock import apply_stock_change, default_location, default_warehouse

MAX_EXPORT_ROWS = 10000

PRODUCT_COLUMNS = ["SKU", "名称", "分类", "物料类型", "单位", "规格", "商品条码", "成本价", "售价", "安全库存", "期初库存"]
PRODUCT_SAMPLE = [
    ["SKU0001", "示例商品", "默认分类", "成品", "件", "规格 A", "6901234567890", 10.5, 19.9, 20, 100]
]

CUSTOMER_COLUMNS = [
    "客户名称",
    "联系人",
    "电话",
    "邮箱",
    "地址",
    "等级",
    "信用额度",
    "备注",
    "期初应收",
    "期初应收日期",
]
CUSTOMER_SAMPLE = [
    ["示例客户", "张三", "13800000000", "demo@example.com", "上海市", "B", 50000, "", 12000, "2026-01-01"]
]

SUPPLIER_COLUMNS = [
    "供应商名称",
    "联系人",
    "电话",
    "邮箱",
    "地址",
    "供应类别",
    "备注",
    "期初应付",
    "期初应付日期",
]
SUPPLIER_SAMPLE = [
    ["示例供应商", "李四", "13900000000", "vendor@example.com", "杭州市", "原材料", "", 30000, "2026-01-01"]
]

SALES_STATUS = {
    "draft": "草稿",
    "pending": "待审批",
    "confirmed": "已确认",
    "completed": "已完成",
    "cancelled": "已取消",
}
PURCHASE_STATUS = {
    "draft": "草稿",
    "pending": "待审批",
    "ordered": "已下单",
    "received": "已入库",
    "cancelled": "已取消",
}
RETURN_STATUS = {"draft": "草稿", "confirmed": "已确认", "cancelled": "已取消"}


def _text(row: dict, key: str, default: str = "") -> str:
    value = row.get(key)
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _error(line: int, reason: str, row: dict) -> dict:
    """拼出一条逐行校验错误；带上原始单元格值，便于「错行导出」。"""
    data = {key: value for key, value in row.items() if key != "_row"}
    return {"row": line, "reason": reason, "data": data}


def _date(value, default: datetime) -> datetime:
    """解析单元格日期；空值取默认日期，非法内容抛 ValueError 交给逐行报错。"""
    if value is None or str(value).strip() == "":
        return default
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value).strip().replace("/", "-")
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"日期不合法：{value}（请用 2026-01-01 格式）") from exc


def _opening(db: Session, partner_type: str, partner_id: int, amount: float, balance_date: datetime, remark: str, username: str) -> bool:
    """写入/覆盖期初余额；返回 True 表示这次是更新已有记录（重复导入不会累加）。"""
    record = db.scalar(
        select(OpeningBalance)
        .where(OpeningBalance.partner_type == partner_type)
        .where(OpeningBalance.partner_id == partner_id)
    )
    if not record:
        db.add(
            OpeningBalance(
                partner_type=partner_type,
                partner_id=partner_id,
                amount=round(amount, 2),
                balance_date=balance_date,
                remark=remark,
                created_by=username,
            )
        )
        db.flush()
        return False
    record.amount = round(amount, 2)
    record.balance_date = balance_date
    record.remark = remark
    record.updated_at = datetime.now()
    db.flush()
    return True


# ---------------- 期初导入 ----------------
def import_products(db: Session, content: bytes, user: User, dry_run: bool = False) -> dict:
    """导入商品与期初库存；期初库存走库存服务写台账，保证账实一致。

    dry_run=True 时只做逐行校验，不写库，用于「导入前校验报告」。
    """
    rows = parse_workbook(content, PRODUCT_COLUMNS)

    def opening_of(row: dict) -> int:
        try:
            return parse_int(row.get("期初库存"))
        except ValueError:
            return 0

    if any(opening_of(row) > 0 for row in rows):
        try:
            default_location(db, default_warehouse(db))
        except Exception as exc:  # noqa: BLE001 统一转成可读提示
            raise ValueError("导入期初库存前，请先在「仓库与库位」中新建可用的仓库和库位") from exc

    existing = set(db.scalars(select(Product.sku)).all())
    created = 0
    skipped = 0
    errors: list[dict] = []
    seen: set[str] = set()

    for row in rows:
        line = row["_row"]
        sku = _text(row, "SKU")
        name = _text(row, "名称")
        if not sku or not name:
            errors.append(_error(line, "SKU 与名称必填", row))
            continue
        if sku in existing or sku in seen:
            skipped += 1
            errors.append(_error(line, f"SKU {sku} 已存在，已跳过", row))
            continue
        try:
            product = Product(
                sku=sku,
                name=name,
                category=_text(row, "分类", "默认分类"),
                product_type=parse_product_type(row.get("物料类型")),
                unit=_text(row, "单位", "件"),
                spec=_text(row, "规格"),
                barcode=_text(row, "商品条码"),
                cost_price=parse_number(row.get("成本价")),
                sale_price=parse_number(row.get("售价")),
                safety_stock=parse_int(row.get("安全库存")),
                stock_qty=0,
            )
            opening = parse_int(row.get("期初库存"))
        except ValueError as exc:
            errors.append(_error(line, str(exc), row))
            continue

        seen.add(sku)
        if dry_run:
            continue
        db.add(product)
        created += 1
        if opening > 0:
            db.flush()
            apply_stock_change(
                db,
                product,
                "in",
                opening,
                reason="期初库存导入",
                biz_type="期初导入",
                username=user.username,
            )

    if dry_run:
        db.rollback()
        return {"total": len(rows), "errors": errors, "error_count": len(errors)}
    db.commit()
    return {"created": created, "updated": 0, "skipped": skipped, "errors": errors, "error_count": len(errors)}


def import_customers(db: Session, content: bytes, user: User, dry_run: bool = False) -> dict:
    """导入客户与期初应收；同名客户不重复建档，但期初余额按导入值覆盖。"""
    rows = parse_workbook(content, CUSTOMER_COLUMNS)
    existing = {c.name: c for c in db.scalars(select(Customer)).all()}
    owner = user.username if own_scope(user.role) else ""
    today = datetime.now()
    created = 0
    updated = 0
    skipped = 0
    errors: list[dict] = []
    seen: set[str] = set()

    for row in rows:
        line = row["_row"]
        name = _text(row, "客户名称")
        if not name:
            errors.append(_error(line, "客户名称必填", row))
            continue
        level = (_text(row, "等级", "C")).upper()
        if level not in {"A", "B", "C"}:
            errors.append(_error(line, f"等级只能是 A/B/C，当前为「{level}」", row))
            continue
        try:
            credit_limit = parse_number(row.get("信用额度"))
            opening = parse_number(row.get("期初应收"))
            opening_date = _date(row.get("期初应收日期"), today)
        except ValueError as exc:
            errors.append(_error(line, str(exc), row))
            continue
        if opening < 0:
            errors.append(_error(line, "期初应收不能为负数", row))
            continue

        customer = existing.get(name)
        if customer or name in seen:
            skipped += 1
            if opening > 0:
                if dry_run:
                    seen.add(name)
                else:
                    _opening(db, "customer", customer.id, opening, opening_date, "客户导入", user.username)
                    updated += 1
            continue

        if dry_run:
            seen.add(name)
            continue

        customer = Customer(
            name=name,
            contact=_text(row, "联系人"),
            phone=_text(row, "电话"),
            email=_text(row, "邮箱"),
            address=_text(row, "地址"),
            level=level,
            credit_limit=credit_limit,
            remark=_text(row, "备注"),
            owner=owner,
        )
        db.add(customer)
        db.flush()
        existing[name] = customer
        created += 1
        if opening > 0:
            _opening(db, "customer", customer.id, opening, opening_date, "客户导入", user.username)
            updated += 1

    if dry_run:
        db.rollback()
        return {"total": len(rows), "errors": errors, "error_count": len(errors)}
    db.commit()
    return {"created": created, "updated": updated, "skipped": skipped, "errors": errors, "error_count": len(errors)}


def import_suppliers(db: Session, content: bytes, user: User, dry_run: bool = False) -> dict:
    """导入供应商与期初应付；同名供应商不重复建档，但期初余额按导入值覆盖。"""
    rows = parse_workbook(content, SUPPLIER_COLUMNS)
    existing = {s.name: s for s in db.scalars(select(Supplier)).all()}
    today = datetime.now()
    created = 0
    updated = 0
    skipped = 0
    errors: list[dict] = []
    seen: set[str] = set()

    for row in rows:
        line = row["_row"]
        name = _text(row, "供应商名称")
        if not name:
            errors.append(_error(line, "供应商名称必填", row))
            continue
        try:
            opening = parse_number(row.get("期初应付"))
            opening_date = _date(row.get("期初应付日期"), today)
        except ValueError as exc:
            errors.append(_error(line, str(exc), row))
            continue
        if opening < 0:
            errors.append(_error(line, "期初应付不能为负数", row))
            continue

        supplier = existing.get(name)
        if supplier or name in seen:
            skipped += 1
            if opening > 0:
                if dry_run:
                    seen.add(name)
                else:
                    _opening(db, "supplier", supplier.id, opening, opening_date, "供应商导入", user.username)
                    updated += 1
            continue

        if dry_run:
            seen.add(name)
            continue

        supplier = Supplier(
            name=name,
            contact=_text(row, "联系人"),
            phone=_text(row, "电话"),
            email=_text(row, "邮箱"),
            address=_text(row, "地址"),
            supply_category=_text(row, "供应类别"),
            remark=_text(row, "备注"),
        )
        db.add(supplier)
        db.flush()
        existing[name] = supplier
        created += 1
        if opening > 0:
            _opening(db, "supplier", supplier.id, opening, opening_date, "供应商导入", user.username)
            updated += 1

    if dry_run:
        db.rollback()
        return {"total": len(rows), "errors": errors, "error_count": len(errors)}
    db.commit()
    return {"created": created, "updated": updated, "skipped": skipped, "errors": errors, "error_count": len(errors)}


# ---------------- 期末导出 ----------------


def _write(wb: Workbook, title: str, columns: list[str], rows, widths: list[int]) -> int:
    """向 write_only 工作簿写一个 Sheet；rows 传入可迭代对象即可逐行落盘。"""
    sheet = wb.create_sheet(title)
    sheet.append(columns)
    count = 0
    for row in rows:
        sheet.append(row)
        count += 1
        if count >= MAX_EXPORT_ROWS:
            break
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    return count


def _dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


def _money(value: float | None) -> float:
    return round(float(value or 0), 2)


def build_export(db: Session, start: datetime, end_exclusive: datetime) -> str:
    """把主数据、库存与期间单据写成多 Sheet Excel 临时文件，返回文件路径。

    用 write_only 工作簿逐行写盘，配合 stream_export 流式下发，避免大单据导出时
    把整个工作簿对象塞进内存。
    """
    wb = Workbook(write_only=True)
    counts: dict[str, int] = {}

    products = db.scalars(select(Product).order_by(Product.sku)).all()
    counts["商品"] = _write(
        wb,
        "商品主数据",
        ["SKU", "名称", "分类", "物料类型", "单位", "规格", "条码", "成本价", "售价", "当前库存", "安全库存", "状态", "创建时间"],
        [
            [
                p.sku,
                p.name,
                p.category,
                PRODUCT_TYPE_LABELS.get(p.product_type, p.product_type),
                p.unit,
                p.spec,
                p.barcode,
                _money(p.cost_price),
                _money(p.sale_price),
                int(p.stock_qty),
                int(p.safety_stock),
                "在售" if p.is_active else "下架",
                _dt(p.created_at),
            ]
            for p in products
        ],
        [16, 24, 14, 10, 8, 16, 16, 12, 12, 12, 12, 8, 18],
    )

    customers = db.scalars(select(Customer).order_by(Customer.name)).all()
    counts["客户"] = _write(
        wb,
        "客户主数据",
        ["客户名称", "联系人", "电话", "邮箱", "地址", "等级", "信用额度", "负责人", "备注", "创建时间"],
        [
            [c.name, c.contact, c.phone, c.email, c.address, c.level, _money(c.credit_limit), c.owner, c.remark, _dt(c.created_at)]
            for c in customers
        ],
        [24, 12, 16, 24, 30, 8, 14, 12, 24, 18],
    )

    suppliers = db.scalars(select(Supplier).order_by(Supplier.name)).all()
    counts["供应商"] = _write(
        wb,
        "供应商主数据",
        ["供应商名称", "联系人", "电话", "邮箱", "地址", "供应类别", "备注", "创建时间"],
        [
            [s.name, s.contact, s.phone, s.email, s.address, s.supply_category, s.remark, _dt(s.created_at)]
            for s in suppliers
        ],
        [24, 12, 16, 24, 30, 14, 24, 18],
    )

    balance_rows = db.execute(
        select(
            Warehouse.name,
            StorageLocation.code,
            StorageLocation.name,
            Product.sku,
            Product.name,
            Product.unit,
            StockBalance.quantity,
        )
        .join(Product, Product.id == StockBalance.product_id)
        .join(Warehouse, Warehouse.id == StockBalance.warehouse_id)
        .join(StorageLocation, StorageLocation.id == StockBalance.location_id)
        .where(StockBalance.quantity > 0)
        .order_by(Warehouse.name, StorageLocation.code, Product.sku)
    ).all()
    counts["库存余额"] = _write(
        wb,
        "库存余额",
        ["仓库", "库位编码", "库位名称", "SKU", "商品", "单位", "数量"],
        [list(row) for row in balance_rows],
        [18, 14, 16, 16, 24, 8, 10],
    )

    logs = db.scalars(
        select(StockLog)
        .options(joinedload(StockLog.product))
        .where(StockLog.created_at >= start)
        .where(StockLog.created_at < end_exclusive)
        .order_by(StockLog.created_at, StockLog.id)
    ).all()
    warehouses = {w.id: w.name for w in db.scalars(select(Warehouse)).all()}
    locations = {l.id: l.code for l in db.scalars(select(StorageLocation)).all()}
    counts["库存流水"] = _write(
        wb,
        "库存流水",
        ["日期", "仓库", "库位", "SKU", "商品", "变动类型", "业务类型", "关联单号", "变动数量", "变动前", "变动后", "原因", "操作人"],
        [
            [
                _dt(log.created_at),
                warehouses.get(log.warehouse_id, ""),
                locations.get(log.location_id, ""),
                log.product.sku if log.product else "",
                log.product.name if log.product else "",
                "入库" if log.change_type == "in" else ("出库" if log.change_type == "out" else "盘点"),
                log.biz_type,
                log.ref_no,
                int(log.change_qty),
                int(log.stock_before),
                int(log.stock_after),
                log.reason,
                log.created_by,
            ]
            for log in logs
        ],
        [18, 16, 12, 16, 24, 10, 14, 18, 10, 10, 10, 24, 12],
    )

    sales_orders = db.scalars(
        select(SalesOrder)
        .options(joinedload(SalesOrder.customer))
        .where(SalesOrder.order_date >= start)
        .where(SalesOrder.order_date < end_exclusive)
        .order_by(SalesOrder.order_date, SalesOrder.id)
    ).all()
    counts["销售订单"] = _write(
        wb,
        "销售订单",
        ["单号", "下单日期", "客户", "状态", "订单金额", "优惠金额", "备注", "创建人", "创建时间"],
        [
            [
                o.order_no,
                _dt(o.order_date),
                o.customer.name if o.customer else "",
                SALES_STATUS.get(o.status, o.status),
                _money(o.total_amount),
                _money(o.discount_amount),
                o.remark,
                o.created_by,
                _dt(o.created_at),
            ]
            for o in sales_orders
        ],
        [20, 18, 24, 10, 14, 14, 24, 12, 18],
    )

    sales_items = db.execute(
        select(
            SalesOrder.order_no,
            SalesOrder.order_date,
            Product.sku,
            Product.name,
            SalesOrderItem.quantity,
            SalesOrderItem.unit_price,
            SalesOrderItem.amount,
        )
        .join(SalesOrderItem, SalesOrderItem.order_id == SalesOrder.id)
        .join(Product, Product.id == SalesOrderItem.product_id)
        .where(SalesOrder.order_date >= start)
        .where(SalesOrder.order_date < end_exclusive)
        .order_by(SalesOrder.order_date, SalesOrder.id, SalesOrderItem.id)
    ).all()
    counts["销售订单明细"] = _write(
        wb,
        "销售订单明细",
        ["单号", "下单日期", "SKU", "商品", "数量", "单价", "金额"],
        [[row[0], _dt(row[1]), row[2], row[3], int(row[4]), _money(row[5]), _money(row[6])] for row in sales_items],
        [20, 18, 16, 24, 10, 12, 14],
    )

    purchase_orders = db.scalars(
        select(PurchaseOrder)
        .options(joinedload(PurchaseOrder.supplier))
        .where(PurchaseOrder.order_date >= start)
        .where(PurchaseOrder.order_date < end_exclusive)
        .order_by(PurchaseOrder.order_date, PurchaseOrder.id)
    ).all()
    counts["采购订单"] = _write(
        wb,
        "采购订单",
        ["单号", "下单日期", "供应商", "状态", "订单金额", "备注", "创建人", "创建时间"],
        [
            [
                o.order_no,
                _dt(o.order_date),
                o.supplier.name if o.supplier else "",
                PURCHASE_STATUS.get(o.status, o.status),
                _money(o.total_amount),
                o.remark,
                o.created_by,
                _dt(o.created_at),
            ]
            for o in purchase_orders
        ],
        [20, 18, 24, 10, 14, 24, 12, 18],
    )

    purchase_items = db.execute(
        select(
            PurchaseOrder.order_no,
            PurchaseOrder.order_date,
            Product.sku,
            Product.name,
            PurchaseOrderItem.quantity,
            PurchaseOrderItem.unit_price,
            PurchaseOrderItem.amount,
        )
        .join(PurchaseOrderItem, PurchaseOrderItem.order_id == PurchaseOrder.id)
        .join(Product, Product.id == PurchaseOrderItem.product_id)
        .where(PurchaseOrder.order_date >= start)
        .where(PurchaseOrder.order_date < end_exclusive)
        .order_by(PurchaseOrder.order_date, PurchaseOrder.id, PurchaseOrderItem.id)
    ).all()
    counts["采购订单明细"] = _write(
        wb,
        "采购订单明细",
        ["单号", "下单日期", "SKU", "商品", "数量", "单价", "金额"],
        [[row[0], _dt(row[1]), row[2], row[3], int(row[4]), _money(row[5]), _money(row[6])] for row in purchase_items],
        [20, 18, 16, 24, 10, 12, 14],
    )

    sales_returns = db.scalars(
        select(SalesReturn)
        .options(joinedload(SalesReturn.customer), joinedload(SalesReturn.order))
        .where(SalesReturn.created_at >= start)
        .where(SalesReturn.created_at < end_exclusive)
        .order_by(SalesReturn.created_at, SalesReturn.id)
    ).all()
    counts["销售退货"] = _write(
        wb,
        "销售退货",
        ["退货单号", "日期", "客户", "原订单号", "状态", "退货金额", "原因", "创建人"],
        [
            [
                r.return_no,
                _dt(r.created_at),
                r.customer.name if r.customer else "",
                r.order.order_no if r.order else "",
                RETURN_STATUS.get(r.status, r.status),
                _money(r.total_amount),
                r.reason,
                r.created_by,
            ]
            for r in sales_returns
        ],
        [20, 18, 24, 20, 10, 14, 24, 12],
    )

    purchase_returns = db.scalars(
        select(PurchaseReturn)
        .options(joinedload(PurchaseReturn.supplier), joinedload(PurchaseReturn.order))
        .where(PurchaseReturn.created_at >= start)
        .where(PurchaseReturn.created_at < end_exclusive)
        .order_by(PurchaseReturn.created_at, PurchaseReturn.id)
    ).all()
    counts["采购退货"] = _write(
        wb,
        "采购退货",
        ["退货单号", "日期", "供应商", "原采购单号", "状态", "退货金额", "原因", "创建人"],
        [
            [
                r.return_no,
                _dt(r.created_at),
                r.supplier.name if r.supplier else "",
                r.order.order_no if r.order else "",
                RETURN_STATUS.get(r.status, r.status),
                _money(r.total_amount),
                r.reason,
                r.created_by,
            ]
            for r in purchase_returns
        ],
        [20, 18, 24, 20, 10, 14, 24, 12],
    )

    payments = db.scalars(
        select(Payment)
        .options(joinedload(Payment.customer))
        .where(Payment.pay_date >= start)
        .where(Payment.pay_date < end_exclusive)
        .order_by(Payment.pay_date, Payment.id)
    ).all()
    counts["收款"] = _write(
        wb,
        "收款记录",
        ["收款单号", "收款日期", "客户", "金额", "方式", "流水号", "状态", "备注", "操作人"],
        [
            [
                p.payment_no,
                _dt(p.pay_date),
                p.customer.name if p.customer else "",
                _money(p.amount),
                p.method,
                p.reference,
                "已入账" if p.status == "posted" else "已作废",
                p.remark,
                p.created_by,
            ]
            for p in payments
        ],
        [20, 18, 24, 14, 12, 20, 10, 24, 12],
    )

    supplier_payments = db.scalars(
        select(SupplierPayment)
        .options(joinedload(SupplierPayment.supplier))
        .where(SupplierPayment.pay_date >= start)
        .where(SupplierPayment.pay_date < end_exclusive)
        .order_by(SupplierPayment.pay_date, SupplierPayment.id)
    ).all()
    counts["付款"] = _write(
        wb,
        "付款记录",
        ["付款单号", "付款日期", "供应商", "金额", "方式", "流水号", "状态", "备注", "操作人"],
        [
            [
                p.payment_no,
                _dt(p.pay_date),
                p.supplier.name if p.supplier else "",
                _money(p.amount),
                p.method,
                p.reference,
                "已入账" if p.status == "posted" else "已作废",
                p.remark,
                p.created_by,
            ]
            for p in supplier_payments
        ],
        [20, 18, 24, 14, 12, 20, 10, 24, 12],
    )

    ar_items = receivables.customer_receivables(db)
    counts["应收账龄"] = _write(
        wb,
        "应收账龄",
        ["客户", "等级", "订单数", "应收总额", "已退", "已收", "期初", "未结余额", "账龄天数", "账龄区间"],
        [
            [
                item["customer"],
                item["level"],
                item["orders"],
                item["total"],
                item["returned"],
                item["paid"],
                item.get("opening", 0.0),
                item["balance"],
                item["age_days"],
                item["bucket"],
            ]
            for item in ar_items
        ],
        [24, 8, 10, 14, 14, 14, 14, 14, 10, 12],
    )

    ap_items = payables.supplier_payables(db)
    counts["应付账龄"] = _write(
        wb,
        "应付账龄",
        ["供应商", "供应类别", "订单数", "应付总额", "已退", "已付", "期初", "未结余额", "账龄天数", "账龄区间"],
        [
            [
                item["supplier"],
                item["category"],
                item["orders"],
                item["total"],
                item["returned"],
                item["paid"],
                item.get("opening", 0.0),
                item["balance"],
                item["age_days"],
                item["bucket"],
            ]
            for item in ap_items
        ],
        [24, 14, 10, 14, 14, 14, 14, 14, 10, 12],
    )

    opening_rows = db.scalars(
        select(OpeningBalance).order_by(OpeningBalance.partner_type, OpeningBalance.partner_id)
    ).all()
    customer_names = {c.id: c.name for c in customers}
    supplier_names = {s.id: s.name for s in suppliers}
    counts["期初余额"] = _write(
        wb,
        "期初余额",
        ["类型", "往来单位", "金额", "期初日期", "备注", "导入人"],
        [
            [
                "期初应收" if row.partner_type == "customer" else "期初应付",
                customer_names.get(row.partner_id, "") if row.partner_type == "customer" else supplier_names.get(row.partner_id, ""),
                _money(row.amount),
                _dt(row.balance_date),
                row.remark,
                row.created_by,
            ]
            for row in opening_rows
        ],
        [12, 24, 14, 18, 24, 12],
    )

    summary = wb.create_sheet("概览", 0)
    summary.append(["项目", "值"])
    summary_rows = [
        ["导出时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
        ["统计期间", f"{start.strftime('%Y-%m-%d')} ~ {end_exclusive.strftime('%Y-%m-%d')}"],
        *[[f"{key}行数", value] for key, value in counts.items()],
        ["应收余额（当前）", receivables.receivable_total(db)],
        ["应付余额（当前）", payables.payable_total(db)],
        ["期初应收合计", _money(sum(r.amount for r in opening_rows if r.partner_type == "customer"))],
        ["期初应付合计", _money(sum(r.amount for r in opening_rows if r.partner_type == "supplier"))],
    ]
    for row in summary_rows:
        summary.append(row)
    summary.column_dimensions["A"].width = 22
    summary.column_dimensions["B"].width = 30

    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        wb.save(path)
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return path


def stream_export(path: str):
    """按块流式读取导出临时文件，供 StreamingResponse 逐段下发并在结束后清理。"""
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 256)
                if not chunk:
                    break
                yield chunk
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def overview(db: Session) -> dict:
    """首页概览：各主数据数量、期初往来合计、仓库是否就绪。"""
    opening = db.execute(
        select(OpeningBalance.partner_type, func.coalesce(func.sum(OpeningBalance.amount), 0))
        .group_by(OpeningBalance.partner_type)
    ).all()
    opening_map = {row[0]: _money(row[1]) for row in opening}
    try:
        default_location(db, default_warehouse(db))
        warehouse_ready = True
    except Exception:  # noqa: BLE001 仓库未就绪时只影响期初库存导入
        warehouse_ready = False
    return {
        "products": int(db.scalar(select(func.count()).select_from(Product)) or 0),
        "customers": int(db.scalar(select(func.count()).select_from(Customer)) or 0),
        "suppliers": int(db.scalar(select(func.count()).select_from(Supplier)) or 0),
        "opening_receivable": opening_map.get("customer", 0.0),
        "opening_payable": opening_map.get("supplier", 0.0),
        "warehouse_ready": warehouse_ready,
    }


def template_for(dataset: str) -> tuple[list[str], list[list], str]:
    templates = {
        "products": (PRODUCT_COLUMNS, PRODUCT_SAMPLE, "商品导入"),
        "customers": (CUSTOMER_COLUMNS, CUSTOMER_SAMPLE, "客户导入"),
        "suppliers": (SUPPLIER_COLUMNS, SUPPLIER_SAMPLE, "供应商导入"),
    }
    if dataset not in templates:
        raise ValueError("不支持的数据集")
    return templates[dataset]


def build_error_report_bytes(dataset: str, errors: list[dict]) -> bytes:
    """把导入前校验的错误行导出为 xlsx：行号 + 原始数据 + 原因。"""
    columns, _, _ = template_for(dataset)
    return build_error_report(errors, columns)


def build_template_bytes(dataset: str) -> bytes:
    columns, sample, sheet_name = template_for(dataset)
    return build_template(columns, sample, sheet_name)
