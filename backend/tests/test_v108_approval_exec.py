"""审批执行层回归：出库、退货、付款、盘点接入通用审批。

运行方式（在 backend 目录下）：
    .venv\\Scripts\\python.exe -m pytest tests/test_v108_approval_exec.py -v
"""
from __future__ import annotations

from datetime import datetime

from app.services import gl


def _rule(client, **kwargs):
    payload = {
        "doc_type": kwargs["doc_type"],
        "name": kwargs.get("name", "规则"),
        "enabled": True,
        "min_amount": kwargs.get("min_amount", 0),
        "max_amount": kwargs.get("max_amount"),
        "level": kwargs.get("level", 1),
        "permission": kwargs.get("permission", ""),
        "level_name": kwargs.get("level_name", ""),
        "mode": kwargs.get("mode", "single"),
        "approvers": kwargs.get("approvers", ""),
        "conditions": kwargs.get("conditions", {}),
    }
    response = client.post("/api/approval-rules", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _customer(client, name="审批客户", level="B") -> int:
    response = client.post("/api/customers", json={"name": name, "level": level})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _confirmed_sales_order(client, api, customer_id: int, quantity: int = 2, price: float = 150.0) -> int:
    created = client.post(
        "/api/sales-orders",
        json={
            "customer_id": customer_id,
            "items": [{"product_id": api.product_id, "quantity": quantity, "unit_price": price}],
        },
    )
    assert created.status_code == 200, created.text
    order_id = created.json()["id"]
    assert client.post(f"/api/sales-orders/{order_id}/status", json={"action": "submit"}).status_code == 200
    approved = client.post(f"/api/sales-orders/{order_id}/status", json={"action": "approve"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "confirmed"
    return order_id


def test_outbound_approval_gates_shipping(api):
    """配了出库审批后：不能直接发货，必须提交审批，通过后自动出库并扣库存。"""
    client = api.client
    _rule(client, doc_type="outbound", name="出库审批", permission="outbound:approve")
    order_id = _confirmed_sales_order(client, api, _customer(client))

    created = client.post("/api/outbounds", json={"sales_order_id": order_id, "items": []})
    assert created.status_code == 200, created.text
    outbound_id = created.json()["id"]

    # 直接发货被拦下
    blocked = client.post(f"/api/outbounds/{outbound_id}/status", json={"action": "ship"})
    assert blocked.status_code == 400, blocked.text
    assert "审批" in blocked.text

    submitted = client.post(f"/api/outbounds/{outbound_id}/status", json={"action": "submit"})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "pending"
    assert submitted.json()["approval_required"] == 1

    approved = client.post(f"/api/outbounds/{outbound_id}/status", json={"action": "approve"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "shipped"

    # 库存 5 - 2 = 3
    product = client.get("/api/products", params={"keyword": "SKU-1001"}).json()["items"][0]
    assert product["stock_qty"] == 3


def test_sales_return_approval_flow(api):
    """销售退货同样受审批门禁：确认前必须走审批。"""
    client = api.client
    _rule(client, doc_type="sales_return", name="退货审批", permission="return:approve")
    customer_id = _customer(client, "退货客户")
    order_id = _confirmed_sales_order(client, api, customer_id)

    outbound = client.post("/api/outbounds", json={"sales_order_id": order_id, "items": []})
    assert outbound.status_code == 200, outbound.text
    shipped = client.post(f"/api/outbounds/{outbound.json()['id']}/status", json={"action": "ship"})
    assert shipped.status_code == 200, shipped.text

    ret = client.post(
        "/api/returns/sales",
        json={
            "order_id": order_id,
            "warehouse_id": api.warehouse_id,
            "reason": "客户退货",
            "items": [{"product_id": api.product_id, "quantity": 1, "unit_price": 150.0}],
        },
    )
    assert ret.status_code == 200, ret.text
    return_id = ret.json()["id"]

    blocked = client.post(f"/api/returns/sales/{return_id}/status", json={"action": "confirm"})
    assert blocked.status_code == 400, blocked.text

    submitted = client.post(f"/api/returns/sales/{return_id}/status", json={"action": "submit"})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "pending"

    approved = client.post(f"/api/returns/sales/{return_id}/status", json={"action": "approve"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "confirmed"


def test_supplier_payment_approval(api):
    """付款单审批：pending 不计入应付，审批通过后转 posted 并完成核销。"""
    client = api.client
    _rule(client, doc_type="supplier_payment", name="付款审批", permission="payable:approve")

    order = client.post(
        "/api/purchase-orders",
        json={
            "supplier_id": api.supplier_id,
            "items": [{"product_id": api.material_id, "quantity": 10, "unit_price": 10.0}],
        },
    )
    assert order.status_code == 200, order.text
    order_id = order.json()["id"]
    assert client.post(f"/api/purchase-orders/{order_id}/status", json={"action": "submit"}).status_code == 200
    assert client.post(f"/api/purchase-orders/{order_id}/status", json={"action": "approve"}).status_code == 200
    received = client.post(f"/api/purchase-orders/{order_id}/status", json={"action": "receive"})
    assert received.status_code == 200, received.text

    payment = client.post(
        "/api/supplier-payments",
        json={"supplier_id": api.supplier_id, "amount": 100.0, "allocations": []},
    )
    assert payment.status_code == 200, payment.text
    payment_id = payment.json()["id"]
    assert payment.json()["status"] == "pending"

    approved = client.post(
        f"/api/supplier-payments/{payment_id}/approve", params={"comment": "同意付款"}
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "posted"

    # 审批通过后核销生效：订单余额应为 0
    open_rows = client.get(f"/api/payables/{api.supplier_id}/orders").json()
    balances = [row["balance"] for row in (open_rows if isinstance(open_rows, list) else open_rows.get("items", []))]
    assert sum(balances) == 0


def test_outbound_scan_increments_shipping_quantity(api):
    """扫码出货：待发货单扫码 +1，且不能超过订单可发数量。"""
    client = api.client
    customer_id = _customer(client, "扫码客户")
    order = client.post(
        "/api/sales-orders",
        json={
            "customer_id": customer_id,
            "items": [{"product_id": api.product_id, "quantity": 3, "unit_price": 100.0}],
        },
    )
    assert order.status_code == 200, order.text
    order_id = order.json()["id"]
    client.post(f"/api/sales-orders/{order_id}/status", json={"action": "submit"})
    client.post(f"/api/sales-orders/{order_id}/status", json={"action": "approve"})
    detail = client.get(f"/api/sales-orders/{order_id}").json()
    order_item_id = detail["items"][0]["id"]

    created = client.post(
        "/api/outbounds",
        json={"sales_order_id": order_id, "items": [{"order_item_id": order_item_id, "quantity": 1}]},
    )
    assert created.status_code == 200, created.text
    outbound_id = created.json()["id"]

    first = client.post(f"/api/outbounds/{outbound_id}/scan", params={"code": "SKU-1001"})
    assert first.status_code == 200, first.text
    assert first.json()["total_quantity"] == 2

    second = client.post(f"/api/outbounds/{outbound_id}/scan", params={"code": "SKU-1001"})
    assert second.status_code == 200, second.text
    assert second.json()["total_quantity"] == 3

    # 已到订单上限，再扫应被拒绝
    third = client.post(f"/api/outbounds/{outbound_id}/scan", params={"code": "SKU-1001"})
    assert third.status_code == 400, third.text


def test_condition_warehouse_and_creator(api):
    """条件分支支持仓库 + 制单人，并能与金额/客户等级组合（AND）。"""
    client = api.client
    for level in (1, 2):
        _rule(
            client,
            doc_type="outbound",
            name=f"仓库制单人-{level}",
            level=level,
            permission="outbound:approve",
            conditions={"warehouse_id": [api.warehouse_id], "created_by": ["admin"]},
        )

    hit = client.get(
        "/api/approval-rules/preview",
        params={
            "doc_type": "outbound",
            "amount": 300,
            "warehouse_id": api.warehouse_id,
            "created_by": "admin",
        },
    ).json()
    assert hit["required"] == 2

    # 仓库不匹配 → 规则不命中，退回单级
    miss_warehouse = client.get(
        "/api/approval-rules/preview",
        params={
            "doc_type": "outbound",
            "amount": 300,
            "warehouse_id": 9999,
            "created_by": "admin",
        },
    ).json()
    assert miss_warehouse["required"] == 1

    miss_creator = client.get(
        "/api/approval-rules/preview",
        params={
            "doc_type": "outbound",
            "amount": 300,
            "warehouse_id": api.warehouse_id,
            "created_by": "someone",
        },
    ).json()
    assert miss_creator["required"] == 1

    # 实际提交时也要命中两级
    order_id = _confirmed_sales_order(client, api, _customer(client))
    outbound = client.post("/api/outbounds", json={"sales_order_id": order_id, "items": []})
    assert outbound.status_code == 200, outbound.text
    submitted = client.post(
        f"/api/outbounds/{outbound.json()['id']}/status", json={"action": "submit"}
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["approval_required"] == 2


def test_condition_amount_plus_customer_level(api):
    """金额区间 + 客户等级组合：只有 A 级且超过 1000 才走两级。"""
    client = api.client
    for level in (1, 2):
        _rule(
            client,
            doc_type="sales",
            name=f"A级大额-{level}",
            min_amount=1000,
            level=level,
            permission="sales:approve",
            conditions={"customer_level": ["A"]},
        )

    a_customer = _customer(client, "A级大额客户", level="A")
    big_order = client.post(
        "/api/sales-orders",
        json={
            "customer_id": a_customer,
            "items": [{"product_id": api.product_id, "quantity": 1, "unit_price": 1500.0}],
        },
    )
    assert big_order.status_code == 200, big_order.text
    submitted = client.post(
        f"/api/sales-orders/{big_order.json()['id']}/status", json={"action": "submit"}
    ).json()
    assert submitted["approval_required"] == 2

    small_order = client.post(
        "/api/sales-orders",
        json={
            "customer_id": a_customer,
            "items": [{"product_id": api.product_id, "quantity": 1, "unit_price": 500.0}],
        },
    )
    assert small_order.status_code == 200, small_order.text
    small = client.post(
        f"/api/sales-orders/{small_order.json()['id']}/status", json={"action": "submit"}
    ).json()
    assert small["approval_required"] == 1  # 金额没到 1000，组合条件不命中

    b_customer = _customer(client, "B级大额客户", level="B")
    b_order = client.post(
        "/api/sales-orders",
        json={
            "customer_id": b_customer,
            "items": [{"product_id": api.product_id, "quantity": 1, "unit_price": 1500.0}],
        },
    )
    assert b_order.status_code == 200, b_order.text
    b_submitted = client.post(
        f"/api/sales-orders/{b_order.json()['id']}/status", json={"action": "submit"}
    ).json()
    assert b_submitted["approval_required"] == 1  # 等级不匹配


def test_template_apply_and_preview(api):
    """行业模板一键套用：写入多级规则，预览按金额命中不同级数，重复套用不叠加。"""
    client = api.client
    templates = client.get("/api/approval-rules/templates").json()
    keys = {item["key"] for item in templates}
    assert {"small", "trade", "manufacture", "strict"} <= keys

    applied = client.post("/api/approval-rules/templates/trade/apply").json()
    assert applied["created"] == 4
    assert set(applied["doc_types"]) == {"sales", "purchase"}

    low = client.get(
        "/api/approval-rules/preview", params={"doc_type": "sales", "amount": 5000}
    ).json()
    assert low["required"] == 1
    high = client.get(
        "/api/approval-rules/preview", params={"doc_type": "sales", "amount": 20000}
    ).json()
    assert high["required"] == 2

    again = client.post("/api/approval-rules/templates/trade/apply").json()
    assert again["removed"] == 4  # 默认先清旧规则，不会越套越多

    missing = client.post("/api/approval-rules/templates/not-exist/apply")
    assert missing.status_code == 404


def test_condition_product_category(api):
    """按商品分类触发审批：命中分类走两级，不命中退回一级。"""
    client = api.client
    for level in (1, 2):
        _rule(
            client,
            doc_type="sales",
            name=f"电动工具-{level}",
            level=level,
            permission="sales:approve",
            conditions={"product_category": ["电动工具"]},
        )
    customer_id = _customer(client, "分类客户")
    order = client.post(
        "/api/sales-orders",
        json={
            "customer_id": customer_id,
            "items": [{"product_id": api.product_id, "quantity": 1, "unit_price": 200.0}],
        },
    )
    assert order.status_code == 200, order.text
    submitted = client.post(
        f"/api/sales-orders/{order.json()['id']}/status", json={"action": "submit"}
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["approval_required"] == 2


def test_condition_product_category_not_matched(api):
    """分类不匹配时规则不生效，仍是单级审批。"""
    client = api.client
    for level in (1, 2):
        _rule(
            client,
            doc_type="sales",
            name=f"五金-{level}",
            level=level,
            permission="sales:approve",
            conditions={"product_category": ["五金"]},
        )
    customer_id = _customer(client, "分类客户2")
    order = client.post(
        "/api/sales-orders",
        json={
            "customer_id": customer_id,
            "items": [{"product_id": api.product_id, "quantity": 1, "unit_price": 200.0}],
        },
    )
    assert order.status_code == 200, order.text
    submitted = client.post(
        f"/api/sales-orders/{order.json()['id']}/status", json={"action": "submit"}
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["approval_required"] == 1


def test_stocktake_approval_with_rules(api):
    """盘点差异审批接入通用审批：有规则时按规则走，通过后才调整库存。"""
    client = api.client
    gl.seed_accounts(api.session())
    _rule(client, doc_type="stocktake", name="盘点审批", permission="stocktake:approve")

    created = client.post("/api/stocktakes", json={"warehouse_id": api.warehouse_id, "remark": "月度盘点"})
    assert created.status_code == 200, created.text
    stocktake_id = created.json()["id"]
    items = created.json()["items"]
    assert items, "盘点单应包含库位商品"

    saved = client.post(
        f"/api/stocktakes/{stocktake_id}/items",
        json={"items": [{"item_id": items[0]["id"], "actual_qty": items[0]["book_qty"] + 1}]},
    )
    assert saved.status_code == 200, saved.text

    submitted = client.post(f"/api/stocktakes/{stocktake_id}/status", json={"action": "complete"})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "pending"
    assert submitted.json()["approval_required"] == 1

    approved = client.post(f"/api/stocktakes/{stocktake_id}/status", json={"action": "approve"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "completed"
