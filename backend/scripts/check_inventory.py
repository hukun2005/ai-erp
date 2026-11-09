"""批次/效期、库存预留与盘点差异审批的自检。

用法：cd backend && python scripts/check_inventory.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app import audit
from app.database import Base, ensure_schema, get_db
from app.main import app
from app.models import Product, StorageLocation, User, Warehouse
from app.security import create_access_token

work = Path(__file__).resolve().parent.parent / "pytest-work"
work.mkdir(exist_ok=True)
db_file = work / "inventory.db"
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
keeper = User(username="keeper", hashed_password="x", full_name="仓管", role="warehouse", is_active=True)
supplier_ok = True
product = Product(sku="RM-1", name="钢材", unit="吨", product_type="raw_material", cost_price=100, sale_price=200)
finished = Product(sku="FG-1", name="成品", unit="台", product_type="finished_good", cost_price=100, sale_price=200)
warehouse = Warehouse(code="MAIN", name="主仓", is_default=True, is_active=True)
db.add_all([admin, keeper, product, finished, warehouse])
db.flush()
location = StorageLocation(warehouse_id=warehouse.id, code="A-01", name="A 区", is_active=True)
db.add(location)
db.flush()
from app.models import Supplier

supplier = Supplier(name="供应商甲", contact="王")
db.add(supplier)
db.commit()
admin_token = create_access_token(admin.id, admin.username)
keeper_token = create_access_token(keeper.id, keeper.username)
ids = {
    "product": product.id,
    "finished": finished.id,
    "warehouse": warehouse.id,
    "location": location.id,
    "supplier": supplier.id,
}
db.close()

client = TestClient(app, raise_server_exceptions=True)
client.headers.update({"Authorization": f"Bearer {admin_token}"})
failures: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    print(("PASS  " if condition else "FAIL  ") + label + ("" if condition else f"  -> {detail}"))
    if not condition:
        failures.append(label)


def stock_of(product_id: int) -> int:
    inner = session()
    try:
        return inner.get(Product, product_id).stock_qty
    finally:
        inner.close()


def balances(product_id: int) -> list[tuple[str, int]]:
    inner = session()
    try:
        from app.models import StockBalance
        from sqlalchemy import select

        rows = inner.scalars(
            select(StockBalance).where(StockBalance.product_id == product_id).order_by(StockBalance.batch_no)
        ).all()
        return [(row.batch_no, row.quantity) for row in rows]
    finally:
        inner.close()


def inbound(batch_no: str, quantity: int, expiry: str | None) -> dict:
    payload = {
        "supplier_id": ids["supplier"],
        "warehouse_id": ids["warehouse"],
        "location_id": ids["location"],
        "items": [
            {
                "product_id": ids["product"],
                "quantity": quantity,
                "unit_price": 100,
                "batch_no": batch_no,
                **({"expiry_date": expiry} if expiry else {}),
            }
        ],
    }
    response = client.post("/api/inbounds", json=payload)
    assert response.status_code == 200, response.text
    created = response.json()
    confirmed = client.post(f"/api/inbounds/{created['id']}/status", json={"action": "confirm"})
    assert confirmed.status_code == 200, confirmed.text
    return created


# ── 1. 批次入库：同商品两批各 5 吨 ────────────────────
soon = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
later = (datetime.now() + timedelta(days=200)).strftime("%Y-%m-%d")
inbound("B-2026-001", 5, soon)
inbound("B-2026-002", 5, later)
check("两批次分别建余额", balances(ids["product"]) == [("B-2026-001", 5), ("B-2026-002", 5)], balances(ids["product"]))
check("总库存=10", stock_of(ids["product"]) == 10, stock_of(ids["product"]))

# ── 2. 效期接口：10 天内到期的批次可见 ────────────────
expiring = client.get("/api/inventory/expiring", params={"days": 30}).json()
check("临期接口命中 1 个批次", expiring["total"] == 1 and expiring["items"][0]["batch_no"] == "B-2026-001", expiring)
check("临期天数计算正确", 8 <= expiring["items"][0]["days_left"] <= 10, expiring["items"][0])

# ── 3. 库存列表带可用量 ──────────────────────────────
listing = client.get("/api/inventory/stock", params={"keyword": "RM-1"}).json()
row = listing["items"][0]
check("列表返回现存/预留/可用", row["stock_qty"] == 10 and row["reserved_qty"] == 0 and row["available_qty"] == 10, row)

# ── 4. 确认订单占用可用量（不扣现存） ─────────────────
order_payload = {
    "items": [{"product_id": ids["product"], "quantity": 4, "unit_price": 200}],
}
# 销售单只能用成品，这里改用成品商品走预留逻辑
inbound_finished = client.post(
    "/api/inbounds",
    json={
        "supplier_id": ids["supplier"],
        "warehouse_id": ids["warehouse"],
        "items": [{"product_id": ids["finished"], "quantity": 3, "unit_price": 100, "batch_no": ""}],
    },
)
stocktake_src = inbound_finished.json() if inbound_finished.status_code == 200 else None
if stocktake_src:
    client.post(f"/api/inbounds/{stocktake_src['id']}/status", json={"action": "confirm"})

# 成品是 finished_good，但入库要求原料；改用库存调整直接给成品建库存
adjust = client.post(
    "/api/inventory/adjust",
    json={
        "product_id": ids["finished"],
        "change_type": "in",
        "quantity": 3,
        "warehouse_id": ids["warehouse"],
        "reason": "初始建库存",
    },
)
check("库存调整给成品建 3 件", adjust.status_code == 200, adjust.text)

customer = client.post("/api/customers", json={"name": "占用客户", "credit_limit": 0}).json()
order = client.post(
    "/api/sales-orders",
    json={"customer_id": customer["id"], "items": [{"product_id": ids["finished"], "quantity": 2, "unit_price": 200}]},
).json()
client.post(f"/api/sales-orders/{order['id']}/status", json={"action": "submit"})
approved = client.post(f"/api/sales-orders/{order['id']}/status", json={"action": "approve"})
check("确认订单成功", approved.status_code == 200, approved.text)
check("确认后现存不变", stock_of(ids["finished"]) == 3, stock_of(ids["finished"]))

listing = client.get("/api/inventory/stock", params={"keyword": "FG-1"}).json()["items"][0]
check("可用量扣掉预留", listing["reserved_qty"] == 2 and listing["available_qty"] == 1, listing)

# 再确认一张 3 件的单：可用只剩 1，应被拒
customer2 = client.post("/api/customers", json={"name": "抢货客户", "credit_limit": 0}).json()
order2 = client.post(
    "/api/sales-orders",
    json={"customer_id": customer2["id"], "items": [{"product_id": ids["finished"], "quantity": 3, "unit_price": 200}]},
).json()
client.post(f"/api/sales-orders/{order2['id']}/status", json={"action": "submit"})
over = client.post(f"/api/sales-orders/{order2['id']}/status", json={"action": "approve"})
check("可用量不足时拒绝确认", over.status_code == 400 and "可用库存不足" in over.text, over.text)

# ── 5. 发货消耗预留 ──────────────────────────────────
outbound = client.post("/api/outbounds", json={"sales_order_id": order["id"]}).json()
client.post(f"/api/outbounds/{outbound['id']}/status", json={"action": "ship"})
check("发货后现存 3-2=1", stock_of(ids["finished"]) == 1, stock_of(ids["finished"]))
listing = client.get("/api/inventory/stock", params={"keyword": "FG-1"}).json()["items"][0]
check("发货后预留清零", listing["reserved_qty"] == 0 and listing["available_qty"] == 1, listing)

# ── 6. FEFO：先扣到期早的批次 ────────────────────────
# 用库存调整出库 6 吨（跨批次），应从 B-001（到期早）扣 5，再扣 B-002 1
adjust_out = client.post(
    "/api/inventory/adjust",
    json={
        "product_id": ids["product"],
        "change_type": "out",
        "quantity": 6,
        "warehouse_id": ids["warehouse"],
        "reason": "领用",
    },
)
check("跨批次出库成功", adjust_out.status_code == 200, adjust_out.text)
remaining = dict(balances(ids["product"]))
check("先到期批次被扣空", remaining.get("B-2026-001", 0) == 0, remaining)
check("后到期批次剩 4", remaining.get("B-2026-002", 0) == 4, remaining)

# ── 7. 盘点差异审批 ──────────────────────────────────
stocktake = client.post("/api/stocktakes", json={"warehouse_id": ids["warehouse"], "remark": "月度盘点"}).json()
items = stocktake["items"]
target = next((item for item in items if item["product_id"] == ids["product"]), items[0])
client.post(
    f"/api/stocktakes/{stocktake['id']}/items",
    json={"items": [{"item_id": target["id"], "actual_qty": target["book_qty"] - 1}]},
)
submitted = client.post(f"/api/stocktakes/{stocktake['id']}/status", json={"action": "complete"})
check("有差异时进入待审批", submitted.status_code == 200 and submitted.json()["status"] == "pending", submitted.text)

before_stock = stock_of(ids["product"])
approved_st = client.post(
    f"/api/stocktakes/{stocktake['id']}/status",
    json={"action": "approve", "remark": "差异属实"},
    headers={"Authorization": f"Bearer {keeper_token}"},
)
check("第二人复核通过", approved_st.status_code == 200 and approved_st.json()["status"] == "completed", approved_st.text)
check("通过后才调整库存", stock_of(ids["product"]) == before_stock - 1, (before_stock, stock_of(ids["product"])))
check("记录审批人", approved_st.json()["approved_by"] == "keeper", approved_st.json().get("approved_by"))

# ── 8. 驳回退回调平后重盘 ────────────────────────────
stocktake2 = client.post("/api/stocktakes", json={"warehouse_id": ids["warehouse"]}).json()
item2 = stocktake2["items"][0]
client.post(
    f"/api/stocktakes/{stocktake2['id']}/items",
    json={"items": [{"item_id": item2["id"], "actual_qty": item2["book_qty"] + 2}]},
)
client.post(f"/api/stocktakes/{stocktake2['id']}/status", json={"action": "complete"})
rejected = client.post(f"/api/stocktakes/{stocktake2['id']}/status", json={"action": "reject", "remark": "重盘"})
check("驳回回到草稿", rejected.status_code == 200 and rejected.json()["status"] == "draft", rejected.text)

# ── 9. 老库迁移：无批次列自动重建 ─────────────────────
legacy_file = work / "legacy_inventory.db"
if legacy_file.exists():
    legacy_file.unlink()
legacy = create_engine(f"sqlite:///{legacy_file.as_posix()}")
with legacy.begin() as conn:
    conn.execute(text("CREATE TABLE warehouses (id INTEGER PRIMARY KEY, code VARCHAR(30), name VARCHAR(80), is_default BOOLEAN, is_active BOOLEAN, address VARCHAR(200), manager VARCHAR(50), created_at DATETIME)"))
    conn.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, sku VARCHAR(50), name VARCHAR(100))"))
    conn.execute(text("CREATE TABLE storage_locations (id INTEGER PRIMARY KEY, warehouse_id INTEGER, code VARCHAR(30), name VARCHAR(80), is_active BOOLEAN)"))
    conn.execute(
        text(
            "CREATE TABLE stock_balances (id INTEGER PRIMARY KEY, product_id INTEGER, warehouse_id INTEGER, "
            "location_id INTEGER, quantity INTEGER, updated_at DATETIME)"
        )
    )
    conn.execute(text("INSERT INTO stock_balances VALUES (1, 1, 1, 1, 7, '2026-01-01 00:00:00')"))
ensure_schema(legacy)
columns = {column["name"] for column in inspect(legacy).get_columns("stock_balances")}
check("老库补齐 batch_no/expiry_date", {"batch_no", "expiry_date"} <= columns, sorted(columns))
with legacy.connect() as conn:
    preserved = conn.execute(text("SELECT quantity, batch_no FROM stock_balances WHERE id = 1")).fetchone()
check("迁移后数量保留", preserved[0] == 7 and preserved[1] == "", preserved)

app.dependency_overrides.clear()
print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
