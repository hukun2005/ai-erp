"""销售出库单：分批发货、扣库存时机、签收与回库。

对应业务规则：出库模式下「审批通过」只确认订单，库存由「确认出库」扣减；
已发货的出库单取消时自动回库；有未取消出库单的订单不能直接取消。
"""
from __future__ import annotations


def _customer(client, name: str) -> int:
    response = client.post("/api/customers", json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _order(client, api, customer_id: int, quantity: int) -> dict:
    response = client.post(
        "/api/sales-orders",
        json={
            "customer_id": customer_id,
            "items": [{"product_id": api.product_id, "quantity": quantity, "unit_price": 100.0}],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _stock(api) -> int:
    db = api.session()
    try:
        from app.models import Product

        return db.get(Product, api.product_id).stock_qty
    finally:
        db.close()


def _act(client, order_id: int, action: str):
    response = client.post(f"/api/sales-orders/{order_id}/status", json={"action": action})
    assert response.status_code == 200, response.text
    return response.json()


def test_approve_does_not_deduct_stock(api, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "SALES_SHIPPING_MODE", "outbound")
    client = api.client
    order = _order(client, api, _customer(client, "出库客户甲"), 2)
    _act(client, order["id"], "submit")
    _act(client, order["id"], "approve")
    # 夹具商品初始库存 5，审批通过后不应变化
    assert _stock(api) == 5


def test_partial_shipment_and_sign(api, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "SALES_SHIPPING_MODE", "outbound")
    client = api.client
    order = _order(client, api, _customer(client, "出库客户乙"), 2)
    _act(client, order["id"], "submit")
    _act(client, order["id"], "approve")
    detail = client.get(f"/api/sales-orders/{order['id']}").json()
    item_id = detail["items"][0]["id"]

    partial = client.post(
        "/api/outbounds",
        json={"sales_order_id": order["id"], "items": [{"order_item_id": item_id, "quantity": 1}]},
    )
    assert partial.status_code == 200, partial.text
    outbound = partial.json()
    assert outbound["status"] == "draft"

    shipped = client.post(f"/api/outbounds/{outbound['id']}/status", json={"action": "ship"})
    assert shipped.status_code == 200, shipped.text
    assert _stock(api) == 4

    # 剩余 1 件仍可继续建单
    rest = client.post("/api/outbounds", json={"sales_order_id": order["id"]})
    assert rest.status_code == 200, rest.text
    assert rest.json()["total_quantity"] == 1

    signed = client.post(
        f"/api/outbounds/{outbound['id']}/status", json={"action": "sign", "signed_by": "李四"}
    )
    assert signed.status_code == 200, signed.text
    assert signed.json()["signed_by"] == "李四"
    assert _stock(api) == 4  # 签收不再动库存


def test_cancel_outbound_returns_stock(api, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "SALES_SHIPPING_MODE", "outbound")
    client = api.client
    order = _order(client, api, _customer(client, "出库客户丙"), 2)
    _act(client, order["id"], "submit")
    _act(client, order["id"], "approve")
    outbound = client.post("/api/outbounds", json={"sales_order_id": order["id"]}).json()
    client.post(f"/api/outbounds/{outbound['id']}/status", json={"action": "ship"})
    assert _stock(api) == 3

    # 有出库单时订单不能直接取消
    blocked = client.post(f"/api/sales-orders/{order['id']}/status", json={"action": "cancel"})
    assert blocked.status_code == 400

    cancelled = client.post(f"/api/outbounds/{outbound['id']}/status", json={"action": "cancel"})
    assert cancelled.status_code == 200, cancelled.text
    assert _stock(api) == 5


def test_direct_mode_keeps_legacy_behaviour(api, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "SALES_SHIPPING_MODE", "direct")
    client = api.client
    order = _order(client, api, _customer(client, "直发客户丁"), 2)
    _act(client, order["id"], "submit")
    _act(client, order["id"], "approve")
    assert _stock(api) == 3
