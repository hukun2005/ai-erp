"""在真实老库副本上演练迁移：确认新表/新列/新索引都补上且数据不丢。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, inspect, text

import app.models  # noqa: F401  确保所有表都注册进 metadata
from app.database import ensure_schema

source = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "AppData/Roaming/AI-ERP/data/erp.db"
if not source.exists():
    print(f"跳过：找不到待演练的数据库 {source}")
    raise SystemExit(0)
work = Path(__file__).with_name("migration-work")
work.mkdir(exist_ok=True)
target = work / "erp-migrate.db"
if target.exists():
    target.unlink()
import shutil

shutil.copyfile(source, target)

engine = create_engine(f"sqlite:///{target.as_posix()}")

with engine.connect() as conn:
    before = {
        "sales_orders": conn.execute(text("SELECT COUNT(*) FROM sales_orders")).scalar(),
        "stock_balances": conn.execute(text("SELECT COUNT(*) FROM stock_balances")).scalar(),
        "stock_qty_sum": conn.execute(text("SELECT COALESCE(SUM(quantity),0) FROM stock_balances")).scalar(),
        "users": conn.execute(text("SELECT COUNT(*) FROM users")).scalar(),
    }

ensure_schema(engine)

with engine.connect() as conn:
    after = {
        "sales_orders": conn.execute(text("SELECT COUNT(*) FROM sales_orders")).scalar(),
        "stock_balances": conn.execute(text("SELECT COUNT(*) FROM stock_balances")).scalar(),
        "stock_qty_sum": conn.execute(text("SELECT COALESCE(SUM(quantity),0) FROM stock_balances")).scalar(),
        "users": conn.execute(text("SELECT COUNT(*) FROM users")).scalar(),
    }

checks = []
checks.append(("订单数不变", before["sales_orders"] == after["sales_orders"], (before, after)))
checks.append(("用户数不变", before["users"] == after["users"], (before, after)))
checks.append(("库存行数与总量不变", before["stock_balances"] == after["stock_balances"] and before["stock_qty_sum"] == after["stock_qty_sum"], (before, after)))

insp = inspect(engine)
tables = set(insp.get_table_names())
for table in ("outbound_orders", "outbound_items", "approval_rules", "credit_policies", "credit_overrides", "stock_reservations", "print_jobs"):
    checks.append((f"新表 {table}", table in tables, sorted(tables)))

columns = {c["name"] for c in insp.get_columns("stock_balances")}
checks.append(("库存批次列", {"batch_no", "expiry_date"} <= columns, sorted(columns)))
columns = {c["name"] for c in insp.get_columns("sales_orders")}
checks.append(("订单审批列", {"approval_required", "approval_approved"} <= columns, sorted(columns)))
columns = {c["name"] for c in insp.get_columns("stocktake_orders")}
checks.append(("盘点审批列", {"submitted_by", "approved_by"} <= columns, sorted(columns)))
columns = {c["name"] for c in insp.get_columns("inbound_items")}
checks.append(("入库效期列", "expiry_date" in columns, sorted(columns)))
indexes = {row["name"] for row in insp.get_indexes("sales_orders")}
checks.append(("台账索引", {"ix_sales_orders_status", "ix_sales_orders_order_date"} <= indexes, sorted(indexes)))

failed = 0
for label, ok, detail in checks:
    print(("PASS  " if ok else "FAIL  ") + label + ("" if ok else f"  -> {detail}"))
    failed += 0 if ok else 1
print()
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)

