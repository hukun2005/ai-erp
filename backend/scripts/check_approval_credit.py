"""分级审批 + 信用策略 + 超额特批的自检。

用法：cd backend && python scripts/check_approval_credit.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import audit
from app.database import Base, get_db
from app.main import app
from app.models import Customer, Product, StockBalance, StorageLocation, User, Warehouse
from app.security import create_access_token, hash_password

work = Path(__file__).resolve().parent.parent / "pytest-work"
work.mkdir(exist_ok=True)
db_file = work / "approval.db"
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
sales_user = User(
    username="sales01", hashed_password=hash_password("x"), full_name="销售", role="sales", is_active=True
)
finance = User(username="fin01", hashed_password="x", full_name="财务", role="finance", is_active=True)
product = Product(sku="FG-9", name="电钻", unit="台", cost_price=100, sale_price=200, stock_qty=1000)
warehouse = Warehouse(code="MAIN", name="主仓", is_default=True, is_active=True)
db.add_all([admin, sales_user, finance, product, warehouse])
db.flush()
location = StorageLocation(warehouse_id=warehouse.id, code="A-01", name="A 区", is_active=True)
db.add(location)
db.flush()
db.add(StockBalance(product_id=product.id, warehouse_id=warehouse.id, location_id=location.id, quantity=1000))
db.commit()
admin_token = create_access_token(admin.id, admin.username)
sales_token = create_access_token(sales_user.id, sales_user.username)
finance_token = create_access_token(finance.id, finance.username)
product_id = product.id
db.close()

client = TestClient(app, raise_server_exceptions=True)
client.headers.update({"Authorization": f"Bearer {admin_token}"})
failures: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    print(("PASS  " if condition else "FAIL  ") + label + ("" if condition else f"  -> {detail}"))
    if not condition:
        failures.append(label)


def as_user(token: str):
    return {"Authorization": f"Bearer {token}"}


def make_rule(**kwargs) -> dict:
    payload = {
        "doc_type": "sales",
        "name": kwargs.get("name", "规则"),
        "enabled": True,
        "min_amount": kwargs.get("min_amount", 0),
        "max_amount": kwargs.get("max_amount", None),
        "level": kwargs.get("level", 1),
        "permission": kwargs.get("permission", "sales:approve"),
        "level_name": kwargs.get("level_name", ""),
    }
    response = client.post("/api/approval-rules", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def make_customer(name: str, level: str = "B", credit_limit: float = 0) -> int:
    response = client.post(
        "/api/customers", json={"name": name, "level": level, "credit_limit": credit_limit}
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def make_order(customer_id: int, quantity: int, price: float) -> int:
    response = client.post(
        "/api/sales-orders",
        json={
            "customer_id": customer_id,
            "items": [{"product_id": product_id, "quantity": quantity, "unit_price": price}],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def act(order_id: int, action: str, token: str = admin_token):
    return client.post(
        f"/api/sales-orders/{order_id}/status", json={"action": action}, headers=as_user(token)
    )


# ── 1. 规则配置与预览 ────────────────────────────────
make_rule(name="常规", min_amount=0, max_amount=10000, level=1, permission="sales:approve", level_name="销售主管")
make_rule(name="大额-一级", min_amount=10000, max_amount=None, level=1, permission="sales:approve", level_name="销售主管")
make_rule(name="大额-二级", min_amount=10000, max_amount=None, level=2, permission="finance:manage", level_name="财务复核")

preview = client.get("/api/approval-rules/preview", params={"doc_type": "sales", "amount": 20000}).json()
check("预览：大额两级", preview["required"] == 2, preview)
small_preview = client.get("/api/approval-rules/preview", params={"doc_type": "sales", "amount": 500}).json()
check("预览：小额一级", small_preview["required"] == 1, small_preview)

bad_rule = client.post(
    "/api/approval-rules",
    json={"doc_type": "sales", "min_amount": 100, "max_amount": 50, "level": 1},
)
check("非法金额区间被拒", bad_rule.status_code == 400, bad_rule.text)

# ── 2. 小额单：一级审批即可确认 ──────────────────────
customer_small = make_customer("小额客户")
small_order = make_order(customer_small, 1, 5000)
acted = act(small_order, "submit")
check("提交后待审批", acted.status_code == 200 and acted.json()["status"] == "pending", acted.text)
check("小额应走 1 级", acted.json().get("approval_required") == 1, acted.json().get("approval_required"))
acted = act(small_order, "approve")
check("一级审批直接确认", acted.status_code == 200 and acted.json()["status"] == "confirmed", acted.text)

# ── 3. 大额单：需要两级 ──────────────────────────────
customer_big = make_customer("大额客户")
big_order = make_order(customer_big, 100, 200)  # 20000
act(big_order, "submit")
first = act(big_order, "approve")
check("大额第一级通过后仍待审批", first.status_code == 200 and first.json()["status"] == "pending", first.text)
check("第一级返回 pending_next_level", first.json().get("pending_next_level") is True, first.text)

# 销售角色没有 finance:manage，无法通过第二级
denied = act(big_order, "approve", token=sales_token)
check("销售角色无法通过第二级", denied.status_code == 403, denied.status_code)

second = act(big_order, "approve", token=finance_token)
check("财务通过第二级后确认", second.status_code == 200 and second.json()["status"] == "confirmed", second.text)

detail = client.get(f"/api/sales-orders/{big_order}").json()
check("详情带审批链", detail["approval"]["required"] == 2 and detail["approval"]["approved"] == 2, detail.get("approval"))

# ── 4. 信用策略按客户等级生效 ────────────────────────
saved = client.put(
    "/api/credit/config", json={"policies": {"*": "warn", "A": "block"}}
)
check("保存信用策略", saved.status_code == 200, saved.text)
config = client.get("/api/credit/config").json()
check("策略回读一致", config["default"] == "warn" and config["levels"].get("A") == "block", config)

bad_policy = client.put("/api/credit/config", json={"policies": {"A": "maybe"}})
check("非法策略被拒", bad_policy.status_code == 400, bad_policy.text)

blocked_customer = make_customer("超额客户", level="A", credit_limit=1000)
blocked_order = make_order(blocked_customer, 10, 200)  # 2000 > 1000
act(blocked_order, "submit")
blocked = act(blocked_order, "approve")
check("A 级客户超额被拦截", blocked.status_code == 400 and "特批" in blocked.text, blocked.text)

# ── 5. 特批放行 ──────────────────────────────────────
override = client.post(
    "/api/credit/overrides", json={"order_id": blocked_order, "reason": "老客户急单"}
)
check("发起特批", override.status_code == 200, override.text)
check("特批待审批", override.json()["status"] == "pending", override.json().get("status"))

still_blocked = act(blocked_order, "approve")
check("特批未批准前仍拦截", still_blocked.status_code == 400, still_blocked.status_code)

approved = client.post(
    f"/api/credit/overrides/{override.json()['id']}/status",
    json={"action": "approve", "comment": "同意"},
)
check("特批通过", approved.status_code == 200 and approved.json()["status"] == "approved", approved.text)

released = act(blocked_order, "approve")
check("特批后放行", released.status_code == 200 and released.json()["status"] == "confirmed", released.text)

# ── 6. 驳回重置审批进度 ──────────────────────────────
customer_reject = make_customer("驳回客户")
reject_order = make_order(customer_reject, 100, 200)
act(reject_order, "submit")
act(reject_order, "approve")
rejected = client.post(
    f"/api/sales-orders/{reject_order}/status", json={"action": "reject", "comment": "价格过低"}
)
check("第二级驳回回到草稿", rejected.status_code == 200 and rejected.json()["status"] == "draft", rejected.text)
check("驳回后审批进度清零", rejected.json().get("approval_approved") in (0, None), rejected.json().get("approval_approved"))

app.dependency_overrides.clear()
print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)

