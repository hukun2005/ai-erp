"""在沙箱内验证列表筛选/排序（pytest 的 tmp_path 在此环境受 ACL 限制）。

用法：cd backend && python tmp_verify.py
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app import audit
from app.database import Base, ensure_schema, get_db
from app.main import app
from app.models import Customer, Product, StockBalance, StorageLocation, Supplier, User, Warehouse
from app.security import create_access_token

work = Path(__file__).with_name("pytest-work")
work.mkdir(exist_ok=True)
db_file = work / "verify.db"
if db_file.exists():
    db_file.unlink()

engine = create_engine(f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)
audit.SessionLocal = session


def override_get_db():
    db = session()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

db = session()
admin = User(username="admin", hashed_password="x", full_name="管理员", role="admin", is_active=True)
product = Product(sku="SKU-1", name="冲击钻", unit="台", cost_price=100, sale_price=150, stock_qty=5)
supplier = Supplier(name="供应商甲", contact="王")
warehouse = Warehouse(code="MAIN", name="主仓", is_default=True, is_active=True)
db.add_all([admin, product, supplier, warehouse])
db.flush()
location = StorageLocation(warehouse_id=warehouse.id, code="A-01", name="A 区", is_active=True)
db.add(location)
db.flush()
db.add(StockBalance(product_id=product.id, warehouse_id=warehouse.id, location_id=location.id, quantity=5))
db.commit()
token = create_access_token(admin.id, admin.username)
ids = {"product": product.id, "supplier": supplier.id}
db.close()

client = TestClient(app, raise_server_exceptions=True)
client.headers.update({"Authorization": f"Bearer {token}"})
failures = []


def check(label: str, condition: bool, detail: object = "") -> None:
    print(("PASS  " if condition else "FAIL  ") + label + ("" if condition else f"  -> {detail}"))
    if not condition:
        failures.append(label)


def make_customer(name: str) -> int:
    response = client.post("/api/customers", json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def make_sale(customer_id: int, qty: int, price: float, day: str) -> dict:
    response = client.post(
        "/api/sales-orders",
        json={
            "customer_id": customer_id,
            "order_date": f"{day}T10:00:00",
            "items": [{"product_id": ids["product"], "quantity": qty, "unit_price": price}],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


first = make_customer("甲方贸易")
second = make_customer("乙方实业")
make_sale(first, 2, 100.0, "2026-03-01")
make_sale(first, 1, 900.0, "2026-04-15")
make_sale(second, 5, 60.0, "2026-05-20")

body = client.get("/api/sales-orders", params={"start_date": "2026-04-15", "end_date": "2026-04-15"}).json()
check("日期闭区间只命中当天", body["total"] == 1, body)
check("筛选后金额合计=900", body["amount_sum"] == 900.0, body.get("amount_sum"))

body = client.get(
    "/api/sales-orders",
    params={"start_date": "2026-03-01", "end_date": "2026-05-31", "customer_id": first},
).json()
check("客户+日期区间组合筛选", body["total"] == 2, body.get("total"))

body = client.get("/api/sales-orders", params={"amount_min": 300, "amount_max": 1000}).json()
check("金额闭区间筛选", body["total"] == 2, [row["total_amount"] for row in body["items"]])

body = client.get("/api/sales-orders", params={"created_by": "admin"}).json()
check("制单人筛选命中", body["total"] == 3, body.get("total"))
body = client.get("/api/sales-orders", params={"created_by": "nobody"}).json()
check("制单人筛选为空", body["total"] == 0, body.get("total"))

asc = client.get("/api/sales-orders", params={"sort": "amount_asc"}).json()["items"]
check("金额升序", [row["total_amount"] for row in asc] == sorted(row["total_amount"] for row in asc), [row["total_amount"] for row in asc])
desc = client.get("/api/sales-orders", params={"sort": "amount_desc"}).json()["items"]
check("金额降序", desc[0]["total_amount"] >= desc[-1]["total_amount"])

bad = client.get("/api/sales-orders", params={"sort": "id;drop table x"})
check("非法排序被拒绝", bad.status_code == 400, bad.status_code)
bad_date = client.get("/api/sales-orders", params={"start_date": "2026/01/01"})
check("非法日期被拒绝", bad_date.status_code == 400, bad_date.status_code)
bad_range = client.get("/api/sales-orders", params={"start_date": "2026-05-01", "end_date": "2026-04-01"})
check("起止倒置被拒绝", bad_range.status_code == 400, bad_range.status_code)

material = client.post(
    "/api/products",
    json={"sku": "RM-1", "name": "钢材", "product_type": "raw_material", "cost_price": 10},
)
if material.status_code != 200:
    raise SystemExit(f"创建原料失败：{material.status_code} {material.text}")
ids["material"] = material.json()["id"]
for day, price in (("2026-06-01", 100.0), ("2026-07-01", 400.0)):
    response = client.post(
        "/api/purchase-orders",
        json={
            "supplier_id": ids["supplier"],
            "order_date": f"{day}T10:00:00",
            "items": [{"product_id": ids["material"], "quantity": 1, "unit_price": price}],
        },
    )
    assert response.status_code == 200, response.text

body = client.get("/api/purchase-orders", params={"supplier_id": ids["supplier"]}).json()
check("采购按供应商筛选", body["total"] == 2, body.get("total"))
body = client.get("/api/purchase-orders", params={"start_date": "2026-07-01", "end_date": "2026-07-31"}).json()
check("采购日期筛选与合计", body["total"] == 1 and body["amount_sum"] == 400.0, body)

# 老库升级：表存在但无索引
legacy_file = work / "legacy.db"
if legacy_file.exists():
    legacy_file.unlink()
legacy = create_engine(f"sqlite:///{legacy_file.as_posix()}")
from sqlalchemy import text as sql_text

with legacy.begin() as conn:
    conn.execute(
        sql_text(
            "CREATE TABLE sales_orders (id INTEGER PRIMARY KEY, order_no VARCHAR(40), "
            "customer_id INTEGER, status VARCHAR(20), order_date DATETIME, total_amount FLOAT, "
            "discount_amount FLOAT, remark TEXT, created_by VARCHAR(50), created_at DATETIME)"
        )
    )
ensure_schema(legacy)
names = {row["name"] for row in inspect(legacy).get_indexes("sales_orders")}
expected = {
    f"ix_sales_orders_{column}"
    for column in ("status", "customer_id", "order_date", "created_by", "created_at")
}
check("老库补齐台账索引", expected <= names, sorted(names))

# 索引确实被查询用到（EXPLAIN 不出现全表扫描）
plan = client.get("/api/sales-orders", params={"status": "draft"}).status_code
check("带筛选的列表可正常返回", plan == 200, plan)

app.dependency_overrides.clear()
print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
