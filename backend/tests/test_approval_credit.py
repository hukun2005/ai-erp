"""分级审批、信用策略与超额特批。

覆盖：按金额命中两级规则、级别权限校验、驳回重置进度、
客户等级策略生效、特批未批准前拦截、批准后放行。
"""
from __future__ import annotations

from app.models import User
from app.security import create_access_token


def _rule(client, **kwargs):
    payload = {
        "doc_type": "sales",
        "name": kwargs.get("name", "规则"),
        "enabled": True,
        "min_amount": kwargs.get("min_amount", 0),
        "max_amount": kwargs.get("max_amount"),
        "level": kwargs.get("level", 1),
        "permission": kwargs.get("permission", "sales:approve"),
        "level_name": kwargs.get("level_name", ""),
    }
    response = client.post("/api/approval-rules", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _customer(client, name: str, level: str = "B", credit_limit: float = 0) -> int:
    response = client.post(
        "/api/customers", json={"name": name, "level": level, "credit_limit": credit_limit}
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _order(client, api, customer_id: int, quantity: int, price: float = 150.0) -> int:
    response = client.post(
        "/api/sales-orders",
        json={
            "customer_id": customer_id,
            "items": [{"product_id": api.product_id, "quantity": quantity, "unit_price": price}],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _finance_token(api) -> str:
    db = api.session()
    try:
        user = User(
            username="fin01", hashed_password="x", full_name="财务", role="finance", is_active=True
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return create_access_token(user.id, user.username)
    finally:
        db.close()


def _two_level_rules(client):
    _rule(client, name="常规", min_amount=0, max_amount=10000, level=1, level_name="销售主管")
    _rule(client, name="大额一级", min_amount=10000, max_amount=None, level=1, level_name="销售主管")
    _rule(
        client,
        name="大额二级",
        min_amount=10000,
        max_amount=None,
        level=2,
        permission="finance:manage",
        level_name="财务复核",
    )


def test_two_level_approval_flow(api):
    client = api.client
    _two_level_rules(client)

    preview = client.get(
        "/api/approval-rules/preview", params={"doc_type": "sales", "amount": 15000}
    ).json()
    assert preview["required"] == 2

    customer = _customer(client, "大额客户")
    order = _order(client, api, customer, 1, price=15000.0)  # 单件 15000，避开库存占用
    client.post(f"/api/sales-orders/{order}/status", json={"action": "submit"})

    first = client.post(f"/api/sales-orders/{order}/status", json={"action": "approve"})
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "pending"
    assert first.json()["pending_next_level"] is True

    finance_token = _finance_token(api)
    second = client.post(
        f"/api/sales-orders/{order}/status",
        json={"action": "approve"},
        headers={"Authorization": f"Bearer {finance_token}"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "confirmed"

    detail = client.get(f"/api/sales-orders/{order}").json()
    assert detail["approval"]["required"] == 2
    assert detail["approval"]["approved"] == 2


def test_single_level_when_amount_small(api):
    client = api.client
    _two_level_rules(client)
    customer = _customer(client, "小额客户")
    order = _order(client, api, customer, 1)  # 150
    submitted = client.post(f"/api/sales-orders/{order}/status", json={"action": "submit"}).json()
    assert submitted["approval_required"] == 1
    approved = client.post(f"/api/sales-orders/{order}/status", json={"action": "approve"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "confirmed"


def test_reject_resets_approval_progress(api):
    client = api.client
    _two_level_rules(client)
    customer = _customer(client, "驳回客户")
    order = _order(client, api, customer, 1, price=15000.0)
    client.post(f"/api/sales-orders/{order}/status", json={"action": "submit"})
    client.post(f"/api/sales-orders/{order}/status", json={"action": "approve"})
    rejected = client.post(
        f"/api/sales-orders/{order}/status", json={"action": "reject", "comment": "价格过低"}
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "draft"
    assert rejected.json()["approval_approved"] == 0


def test_credit_policy_by_level_and_override(api):
    client = api.client
    saved = client.put("/api/credit/config", json={"policies": {"*": "warn", "A": "block"}})
    assert saved.status_code == 200, saved.text
    config = client.get("/api/credit/config").json()
    assert config["default"] == "warn"
    assert config["levels"].get("A") == "block"

    customer = _customer(client, "超额客户", level="A", credit_limit=100)
    order = _order(client, api, customer, 2)  # 300 > 100
    client.post(f"/api/sales-orders/{order}/status", json={"action": "submit"})
    blocked = client.post(f"/api/sales-orders/{order}/status", json={"action": "approve"})
    assert blocked.status_code == 400
    assert "特批" in blocked.text

    override = client.post(
        "/api/credit/overrides", json={"order_id": order, "reason": "老客户急单"}
    )
    assert override.status_code == 200, override.text

    still_blocked = client.post(f"/api/sales-orders/{order}/status", json={"action": "approve"})
    assert still_blocked.status_code == 400

    approved = client.post(
        f"/api/credit/overrides/{override.json()['id']}/status",
        json={"action": "approve", "comment": "同意"},
    )
    assert approved.status_code == 200, approved.text

    released = client.post(f"/api/sales-orders/{order}/status", json={"action": "approve"})
    assert released.status_code == 200, released.text
    assert released.json()["status"] == "confirmed"

