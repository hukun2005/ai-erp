from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

# 台账与报表高频过滤的列。老库升级时 create_all 不会补索引，这里显式补，
# 名称与 SQLAlchemy 的默认约定一致，新库已有同名索引时不会重复创建。
LEDGER_INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sales_orders", ("status", "customer_id", "order_date", "created_by", "created_at")),
    ("purchase_orders", ("status", "supplier_id", "order_date", "created_by", "created_at")),
    ("sales_order_items", ("order_id", "product_id")),
    ("purchase_order_items", ("order_id", "product_id")),
    ("stock_logs", ("created_at",)),
    ("payments", ("created_at",)),
    ("supplier_payments", ("created_at",)),
    ("audit_logs", ("created_at",)),
    # 审批 / 盘点明细高频过滤：老库补单列索引避免全表扫描
    ("approval_logs", ("order_type", "order_id")),
    ("stocktake_items", ("stocktake_id",)),
    ("stocktake_orders", ("created_at",)),
    ("stock_balances", ("expiry_date",)),
)


class Base(DeclarativeBase):
    pass


def _ensure_ledger_indexes(conn) -> None:
    """给台账类表补单列索引；表不存在时跳过（新库由 create_all 建齐）。"""
    tables = set(inspect(conn).get_table_names())
    for table, columns in LEDGER_INDEXES:
        if table not in tables:
            continue
        existing = {column["name"] for column in inspect(conn).get_columns(table)}
        for column in columns:
            if column not in existing:
                continue
            conn.execute(
                text(f"CREATE INDEX IF NOT EXISTS ix_{table}_{column} ON {table} ({column})")
            )


def _migrate_stock_balance_batches(conn) -> None:
    """把库存余额表升级到「批次维度」。

    唯一约束要从（商品,仓库,库位）改成加批次，SQLite 不支持直接改约束，
    只能重建表：改名旧表 → 建新表 → 拷数据（批次为空）→ 删旧表 → 补索引。
    """
    tables = set(inspect(conn).get_table_names())
    if "stock_balances" not in tables:
        return
    columns = {column["name"] for column in inspect(conn).get_columns("stock_balances")}
    if "batch_no" in columns:
        return
    conn.execute(text("ALTER TABLE stock_balances RENAME TO stock_balances_legacy"))
    conn.execute(
        text(
            """
            CREATE TABLE stock_balances (
                id INTEGER NOT NULL PRIMARY KEY,
                product_id INTEGER NOT NULL REFERENCES products (id),
                warehouse_id INTEGER NOT NULL REFERENCES warehouses (id),
                location_id INTEGER NOT NULL REFERENCES storage_locations (id),
                batch_no VARCHAR(60) NOT NULL DEFAULT '',
                expiry_date DATETIME,
                quantity INTEGER NOT NULL DEFAULT 0,
                updated_at DATETIME NOT NULL,
                CONSTRAINT uq_stock_balance UNIQUE (product_id, warehouse_id, location_id, batch_no)
            )
            """
        )
    )
    conn.execute(
        text(
            """
            INSERT INTO stock_balances
                (id, product_id, warehouse_id, location_id, batch_no, expiry_date, quantity, updated_at)
            SELECT id, product_id, warehouse_id, location_id, '', NULL, quantity, updated_at
            FROM stock_balances_legacy
            """
        )
    )
    conn.execute(text("DROP TABLE stock_balances_legacy"))
    for column in ("product_id", "warehouse_id", "location_id", "batch_no"):
        conn.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS ix_stock_balances_{column} "
                f"ON stock_balances ({column})"
            )
        )


def _ensure_inventory_columns(conn, tables: set[str]) -> None:
    """批次效期与盘点审批新增的列，老库升级时补齐。"""
    if "inbound_items" in tables:
        columns = {column["name"] for column in inspect(conn).get_columns("inbound_items")}
        if "expiry_date" not in columns:
            conn.execute(text("ALTER TABLE inbound_items ADD COLUMN expiry_date DATETIME"))
    if "stock_logs" in tables:
        columns = {column["name"] for column in inspect(conn).get_columns("stock_logs")}
        if "batch_no" not in columns:
            conn.execute(text("ALTER TABLE stock_logs ADD COLUMN batch_no VARCHAR(60) DEFAULT ''"))
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_stock_logs_batch_no ON stock_logs (batch_no)")
        )
    if "stocktake_orders" in tables:
        columns = {column["name"] for column in inspect(conn).get_columns("stocktake_orders")}
        for name, ddl in (
            ("submitted_by", "VARCHAR(50) DEFAULT ''"),
            ("submitted_at", "DATETIME"),
            ("approved_by", "VARCHAR(50) DEFAULT ''"),
            ("approved_at", "DATETIME"),
            ("approve_comment", "VARCHAR(300) DEFAULT ''"),
        ):
            if name not in columns:
                conn.execute(text(f"ALTER TABLE stocktake_orders ADD COLUMN {name} {ddl}"))


def _columns(conn, tables: set[str], table: str) -> set[str]:
    """取列名；表不存在时返回空集合（老库可能缺表，不能让迁移整段崩掉）。"""
    if table not in tables:
        return set()
    return {column["name"] for column in inspect(conn).get_columns(table)}


def ensure_schema(target_engine=None) -> None:
    """建表 + 给老库补齐新增字段与索引。

    create_all 只会新建缺失的表，不会给已有表加列或索引；升级安装时旧库里
    users 表没有 phone 字段、sales_orders 除单号外没有任何索引，列表页和
    报表会退化成全表扫描，所以这里显式补齐。
    """
    target = target_engine or engine
    Base.metadata.create_all(bind=target)
    with target.begin() as conn:
        _migrate_stock_balance_batches(conn)
        _ensure_ledger_indexes(conn)
        tables = set(inspect(conn).get_table_names())
        _ensure_inventory_columns(conn, tables)
        if "users" not in tables:
            return
        columns = _columns(conn, tables, "users")
        if "phone" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN phone VARCHAR(30)"))
        # 商品新增物料类型：老库升级后默认全部视为成品
        product_columns = _columns(conn, tables, "products")
        if "product_type" not in product_columns:
            conn.execute(
                text(
                    "ALTER TABLE products ADD COLUMN product_type VARCHAR(20) "
                    "DEFAULT 'finished_good'"
                )
            )
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_products_product_type ON products (product_type)")
        )
        # 移动加权平均成本：老库升级补列，并按既有成本价一次性回填
        if "avg_cost" not in product_columns:
            conn.execute(text("ALTER TABLE products ADD COLUMN avg_cost FLOAT DEFAULT 0"))
            conn.execute(text("UPDATE products SET avg_cost = cost_price WHERE cost_price > 0"))
        # SQLite 的 UNIQUE 索引允许多个 NULL，老账号不占用手机号
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_phone ON users (phone)"))
        # 单据模板新增 Word 模板文件字段：老库升级后也要能上传 .docx
        template_columns = _columns(conn, tables, "document_templates")
        for name in ("word_file", "word_filename"):
            if name not in template_columns and "document_templates" in tables:
                conn.execute(
                    text(f"ALTER TABLE document_templates ADD COLUMN {name} VARCHAR(200) DEFAULT ''")
                )
        # 发票新增审核标记：老库升级后默认未审核（缺表时跳过，create_all 会补表）
        invoice_columns = _columns(conn, tables, "invoices")
        for name, ddl in (
            ("reviewed", "BOOLEAN DEFAULT 0"),
            ("reviewed_by", "VARCHAR(50) DEFAULT ''"),
            ("reviewed_at", "DATETIME"),
        ):
            if "invoices" in tables and name not in invoice_columns:
                conn.execute(text(f"ALTER TABLE invoices ADD COLUMN {name} {ddl}"))
        # 多级审批：老库的订单/出库/退货/收付款/盘点表没有这两列，审批规则生效前先补上
        for table in (
            "sales_orders",
            "purchase_orders",
            "outbound_orders",
            "sales_returns",
            "purchase_returns",
            "payments",
            "supplier_payments",
            "stocktake_orders",
        ):
            order_columns = _columns(conn, tables, table)
            if not order_columns:
                continue
            if "approval_required" not in order_columns:
                conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN approval_required INTEGER DEFAULT 0")
                )
            if "approval_approved" not in order_columns:
                conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN approval_approved INTEGER DEFAULT 0")
                )
        # 采购申请接入多级审批：老库补齐这两列
        req_columns = _columns(conn, tables, "purchase_requisitions")
        if req_columns and "approval_required" not in req_columns:
            conn.execute(
                text("ALTER TABLE purchase_requisitions ADD COLUMN approval_required INTEGER DEFAULT 0")
            )
            conn.execute(
                text("ALTER TABLE purchase_requisitions ADD COLUMN approval_approved INTEGER DEFAULT 0")
            )
        # 审批日志记录级别，旧记录默认 0（单级流程）
        log_columns = _columns(conn, tables, "approval_logs")
        if log_columns and "level" not in log_columns:
            conn.execute(text("ALTER TABLE approval_logs ADD COLUMN level INTEGER DEFAULT 0"))
        if log_columns and "principal" not in log_columns:
            conn.execute(text("ALTER TABLE approval_logs ADD COLUMN principal VARCHAR(50) DEFAULT ''"))
        # 审批轨迹按 (单据类型, 单据ID) 翻日志，加组合索引加速明细查询
        if "approval_logs" in tables:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_approval_logs_type_order "
                    "ON approval_logs (order_type, order_id)"
                )
            )
        # 审批规则新增会签/或签与指定审批人：老库升级默认单人审批
        rule_columns = _columns(conn, tables, "approval_rules")
        for name, ddl in (
            ("mode", "VARCHAR(10) DEFAULT 'single'"),
            ("approvers", "VARCHAR(300) DEFAULT ''"),
        ):
            if "approval_rules" in tables and name not in rule_columns:
                conn.execute(text(f"ALTER TABLE approval_rules ADD COLUMN {name} {ddl}"))
        # 照片识别：入库/采购/销售单都留原图与识别原文，老库补齐这些列
        ocr_columns = (
            ("photo_path", "VARCHAR(300) DEFAULT ''"),
            ("photo_name", "VARCHAR(200) DEFAULT ''"),
            ("photo_paths", "TEXT DEFAULT ''"),
            ("ocr_status", "VARCHAR(20) DEFAULT ''"),
            ("ocr_raw", "TEXT DEFAULT ''"),
        )
        for table in ("inbound_orders", "purchase_orders", "sales_orders"):
            order_columns = _columns(conn, tables, table)
            if not order_columns:
                continue
            for name, ddl in ocr_columns:
                if name not in order_columns:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        # 商品条码：老库升级补列，扫码录单用
        if "barcode" not in product_columns:
            conn.execute(text("ALTER TABLE products ADD COLUMN barcode VARCHAR(50) DEFAULT ''"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_barcode ON products (barcode)"))
        # 价税分离：订单新增税率，>0 时 total_amount 视为价税合计；老库默认 0（不含税，行为不变）
        for table in ("sales_orders", "purchase_orders"):
            order_columns = _columns(conn, tables, table)
            if order_columns and "tax_rate" not in order_columns:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN tax_rate FLOAT DEFAULT 0"))
        # 审批规则条件分支：老库升级默认无条件（仅按金额区间）
        if "approval_rules" in tables and "conditions" not in rule_columns:
            conn.execute(text("ALTER TABLE approval_rules ADD COLUMN conditions VARCHAR(500) DEFAULT ''"))
        # 盘点生成的调整单：补来源字段，用于追溯与去重
        if "stock_adjustments" in tables:
            adjust_columns = _columns(conn, tables, "stock_adjustments")
            if "source_type" not in adjust_columns:
                conn.execute(
                    text("ALTER TABLE stock_adjustments ADD COLUMN source_type VARCHAR(20) DEFAULT ''")
                )
            if "source_no" not in adjust_columns:
                conn.execute(
                    text("ALTER TABLE stock_adjustments ADD COLUMN source_no VARCHAR(40) DEFAULT ''")
                )
        # 审批超时两段式：老库/中间态补提醒、升级两列（空=不升级）
        if "approval_slas" in tables:
            sla_columns = _columns(conn, tables, "approval_slas")
            if "remind_hours" not in sla_columns:
                conn.execute(
                    text("ALTER TABLE approval_slas ADD COLUMN remind_hours INTEGER DEFAULT 24")
                )
            if "escalate_hours" not in sla_columns:
                conn.execute(
                    text("ALTER TABLE approval_slas ADD COLUMN escalate_hours INTEGER")
                )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
