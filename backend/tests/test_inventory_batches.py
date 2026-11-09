"""批次/效期、库存预留与盘点差异审批。

夹具里的成品库存 5 件；批次相关的用例单独建原料，走入库单确认入库。
"""
from __future__ import annotations

from datetime import datetime, timedelta


def _material(client) -> int:
    response = client.post(
        "/api/products",
        json={"sku": "RM-1001", "name": "钢材", "product_type": "raw_material", "cost_price": 10},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _inbound(client, api, product_id: int, batch_no: str, quantity: int, expiry: str | None) -> None:
    payload = {
        "supplier_id": api.supplier_id,
        "warehouse_id": api.warehouse_id,
        "location_id": api.location_id,
        "items": [
            {
                "product_id": product_id,
                "quantity": quantity,
                "unit_price": 10,
                "batch_no": batch_no,
                **({"expiry_date": expiry} if expiry else {}),
            }
        ],
    }
    created = client.post("/api/inbounds", json=payload)
    assert created.status_code == 200, created.text
    confirmed = client.post(f"/api/inbounds/{created.json()['id']}/status", json={"action": "confirm"})
    assert confirmed.status_code == 200, confirmed.text


def _balances(api, product_id: int) -> dict[str, int]:
    from sqlalchemy import select

    from app.models import StockBalance

    db = api.session()
    try:
        rows = db.scalars(select(StockBalance).where(StockBalance.product_id == product_id)).all()
        return {row.batch_no: row.quantity for row in rows}
    finally:
        db.close()


def test_batch_inbound_and_fefo_deduction(api):
    client = api.client
    material = _material(client)
    soon = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
    later = (datetime.now() + timedelta(days=300)).strftime("%Y-%m-%d")
    _inbound(client, api, material, "B-001", 4, soon)
    _inbound(client, api, material, "B-002", 6, later)
    assert _balances(api, material) == {"B-001": 4, "B-002": 6}

    # 临期接口能捞到 5 天内到期的批次
    expiring = client.get("/api/inventory/expiring", params={"days": 30}).json()
    assert any(row["batch_no"] == "B-001" for row in expiring["items"])
    assert expiring["expired"] == 0

    # 出库 5：先扣到期早的 4，再从后到期扣 1
    response = client.post(
        "/api/inventory/adjust",
        json={
            "product_id": material,
            "change_type": "out",
            "quantity": 5,
            "warehouse_id": api.warehouse_id,
            "reason": "领用",
        },
    )
    assert response.status_code == 200, response.text
    assert _balances(api, material) == {"B-001": 0, "B-002": 5}


def test_order_reserves_available_quantity(api):
    client = api.client
    customer = client.post("/api/customers", json={"name": "预留客户", "credit_limit": 0}).json()
    order = client.post(
        "/api/sales-orders",
        json={
            "customer_id": customer["id"],
            "items": [{"product_id": api.product_id, "quantity": 2, "unit_price": 100}],
        },
    ).json()
    client.post(f"/api/sales-orders/{order['id']}/status", json={"action": "submit"})
    approved = client.post(f"/api/sales-orders/{order['id']}/status", json={"action": "approve"})
    assert approved.status_code == 200, approved.text

    row = client.get("/api/inventory/stock", params={"keyword": "SKU-1001"}).json()["items"][0]
    assert row["stock_qty"] == 5  # 现存不变
    assert row["reserved_qty"] == 2  # 已占用
    assert row["available_qty"] == 3

    # 再确认 4 件超出可用量，应被拒
    order2 = client.post(
        "/api/sales-orders",
        json={
            "customer_id": customer["id"],
            "items": [{"product_id": api.product_id, "quantity": 4, "unit_price": 100}],
        },
    ).json()
    client.post(f"/api/sales-orders/{order2['id']}/status", json={"action": "submit"})
    rejected = client.post(f"/api/sales-orders/{order2['id']}/status", json={"action": "approve"})
    assert rejected.status_code == 400
    assert "可用库存不足" in rejected.text


def test_stocktake_diff_needs_second_approval(api):
    client = api.client
    created = client.post("/api/stocktakes", json={"warehouse_id": api.warehouse_id, "remark": "月盘"}).json()
    item = created["items"][0]
    client.post(
        f"/api/stocktakes/{created['id']}/items",
        json={"items": [{"item_id": item["id"], "actual_qty": item["book_qty"] - 1}]},
    )
    submitted = client.post(f"/api/stocktakes/{created['id']}/status", json={"action": "complete"})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "pending"

    # 提交人自己不能再点通过（admin 放行），换仓管角色复核
    from app.models import User
    from app.security import create_access_token

    db = api.session()
    try:
        keeper = User(username="keeper", hashed_password="x", full_name="仓管", role="warehouse", is_active=True)
        db.add(keeper)
        db.commit()
        db.refresh(keeper)
        token = create_access_token(keeper.id, keeper.username)
    finally:
        db.close()

    approved = client.post(
        f"/api/stocktakes/{created['id']}/status",
        json={"action": "approve", "remark": "差异属实"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "completed"
    assert approved.json()["approved_by"] == "keeper"
