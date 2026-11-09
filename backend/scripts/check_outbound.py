"""出库单（分批发货 / 物流 / 签收 / 回库）的端到端自检。

用法：cd backend && python scripts/check_outbound.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import audit
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import Product, StockBalance, StorageLocation, User, Warehouse
from app.security import create_access_token

work = Path(__file__).resolve().parent.parent / "pytest-work"
work.mkdir(exist_ok=True)
db_file = work / "outbound.db"
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
product = Product(sku="FG-1", name="冲击钻", unit="台", cost_price=100, sale_price=150, stock_qty=50, product_type="finished_good")
warehouse = Warehouse(code="MAIN", name="主仓", is_default=True, is_active=True)
db.add_all([admin, product, warehouse])
db.flush()
location = StorageLocation(warehouse_id=warehouse.id, code="A-01", name="A 区", is_active=True)
db.add(location)
db.flush()
db.add(StockBalance(product_id=product.id, warehouse_id=warehouse.id, location_id=location.id, quantity=50))
db.commit()
token = create_access_token(admin.id, admin.username)
product_id, warehouse_id = product.id, warehouse.id
db.close()

client = TestClient(app, raise_server_exceptions=True)
client.headers.update({"Authorization": f"Bearer {token}"})
failures: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    print(("PASS  " if condition else "FAIL  ") + label + ("" if condition else f"  -> {detail}"))
    if not condition:
        failures.append(label)


def stock() -> int:
    inner = session()
    try:
        return inner.get(Product, product_id).stock_qty
    finally:
        inner.close()


def make_order(quantity: int, price: float = 150.0) -> int:
    customer = client.post("/api/customers", json={"name": f"客户{quantity}"}).json()
    response = client.post(
        "/api/sales-orders",
        json={
            "customer_id": customer["id"],
            "items": [{"product_id": product_id, "quantity": quantity, "unit_price": price}],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def act(order_id: int, action: str) -> dict:
    response = client.post(f"/api/sales-orders/{order_id}/status", json={"action": action})
    assert response.status_code == 200, response.text
    return response.json()


settings.SALES_SHIPPING_MODE = "outbound"

# 1) 审批通过只确认订单，不扣库存
order_id = make_order(3)
act(order_id, "submit")
act(order_id, "approve")
check("出库模式：审批不扣库存", stock() == 50, stock())

# 2) 建出库单（整单）
created = client.post("/api/outbounds", json={"sales_order_id": order_id})
check("生成出库单", created.status_code == 200, created.text)
outbound = created.json()
check("出库单数量=3", outbound["total_quantity"] == 3, outbound.get("total_quantity"))
check("出库单初始状态=待发货", outbound["status"] == "draft", outbound.get("status"))

# 3) 重复建单应被拒（已无剩余）
duplicate = client.post("/api/outbounds", json={"sales_order_id": order_id})
check("剩余为 0 时拒绝重复建单", duplicate.status_code == 400, duplicate.text)

# 4) 发货扣库存
shipped = client.post(f"/api/outbounds/{outbound['id']}/status", json={"action": "ship"})
check("确认出库成功", shipped.status_code == 200, shipped.text)
check("发货后库存 50-3=47", stock() == 47, stock())

# 5) 签收不动库存
signed = client.post(
    f"/api/outbounds/{outbound['id']}/status", json={"action": "sign", "signed_by": "张三"}
)
check("登记签收成功", signed.status_code == 200, signed.text)
check("签收后库存不变", stock() == 47, stock())
check("签收人写入", signed.json()["signed_by"] == "张三", signed.json().get("signed_by"))

# 6) 订单发货进度
detail = client.get(f"/api/sales-orders/{order_id}").json()
check("订单进度：已发=3", detail["shipment"]["shipped_quantity"] == 3, detail["shipment"])
check("订单详情带出库单", len(detail["outbounds"]) == 1, detail.get("outbounds"))
check("订单详情带发货模式", detail["shipping_mode"] == "outbound", detail.get("shipping_mode"))

# 7) 有出库单时订单不可直接取消
blocked = client.post(f"/api/sales-orders/{order_id}/status", json={"action": "cancel"})
check("有出库单时禁止取消订单", blocked.status_code == 400, blocked.text)

# 8) 部分发货：再开 1 件，只发 1 件后仍有剩余
order2 = make_order(3)
act(order2, "submit")
act(order2, "approve")
partial = client.post(
    "/api/outbounds",
    json={"sales_order_id": order2, "items": [{"order_item_id": client.get(f"/api/sales-orders/{order2}").json()["items"][0]["id"], "quantity": 1}]},
)
check("部分发货出库单", partial.status_code == 200, partial.text)
partial_id = partial.json()["id"]
client.post(f"/api/outbounds/{partial_id}/status", json={"action": "ship"})
check("部分发货后库存 47-1=46", stock() == 46, stock())
remaining = client.post("/api/outbounds", json={"sales_order_id": order2})
check("剩余 2 件可继续建单", remaining.status_code == 200 and remaining.json()["total_quantity"] == 2, remaining.text)

# 9) 库存不足时整单失败，且不会半单扣减
# 先确认一张 3 件的单（占用库存），再把库存人为调低到不足，模拟「确认后库存被修正」
order3 = make_order(3)
act(order3, "submit")
act(order3, "approve")
current = stock()
adjust = client.post(
    "/api/inventory/adjust",
    json={
        "product_id": product_id,
        "change_type": "out",
        "quantity": current - 1,
        "warehouse_id": warehouse_id,
        "reason": "模拟库存缩水",
    },
)
check("库存被调低到 1 件", adjust.status_code == 200 and stock() == 1, (adjust.text, stock()))
big = client.post(
    "/api/outbounds",
    json={
        "sales_order_id": order3,
        "items": [
            {
                "order_item_id": client.get(f"/api/sales-orders/{order3}").json()["items"][0]["id"],
                "quantity": 3,
            }
        ],
    },
)
check("库存不足仍可建出库单（草稿不扣库存）", big.status_code == 200, big.text)
ship_big = client.post(f"/api/outbounds/{big.json()['id']}/status", json={"action": "ship"})
check("库存不足时发货失败", ship_big.status_code == 400, ship_big.text)
check("失败后库存未变", stock() == 1, stock())

# 10) 取消已发货出库单 → 回库
cancel = client.post(f"/api/outbounds/{partial_id}/status", json={"action": "cancel"})
check("取消出库单成功", cancel.status_code == 200, cancel.text)
check("取消后库存 1+1=2", stock() == 2, stock())

# 11) 出库单列表筛选
listing = client.get("/api/outbounds", params={"status": "cancelled"}).json()
check("按状态筛出库单", listing["total"] >= 1, listing.get("total"))
bad_sort = client.get("/api/outbounds", params={"sort": "id;drop"})
check("出库单非法排序被拒", bad_sort.status_code == 400, bad_sort.status_code)

# 12) direct 模式保持旧行为（先补 10 件库存，避开占用）
client.post(
    "/api/inventory/adjust",
    json={
        "product_id": product_id,
        "change_type": "in",
        "quantity": 10,
        "warehouse_id": warehouse_id,
        "reason": "补库存",
    },
)
settings.SALES_SHIPPING_MODE = "direct"
order4 = make_order(2)
act(order4, "submit")
before_direct = stock()
act(order4, "approve")
check("direct 模式审批即扣库存", stock() == before_direct - 2, (before_direct, stock()))
settings.SALES_SHIPPING_MODE = "outbound"

app.dependency_overrides.clear()
print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)


