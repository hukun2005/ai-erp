"""台账列表的筛选、排序与索引迁移测试。

覆盖三件事：筛选项真正生效（日期闭区间、金额区间、制单人、往来单位）、
排序白名单生效、老库升级时索引被补上。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine, inspect, text

from app.database import LEDGER_INDEXES, ensure_schema


def _customer(client, name: str, level: str = "B") -> int:
    response = client.post("/api/customers", json={"name": name, "level": level, "credit_limit": 0})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _order(client, customer_id: int, product_id: int, quantity: int, price: float, day: str) -> dict:
    response = client.post(
        "/api/sales-orders",
        json={
            "customer_id": customer_id,
            "order_date": day,
            "items": [{"product_id": product_id, "quantity": quantity, "unit_price": price}],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_sales_list_filters(api):
    client = api.client
    first = _customer(client, "甲方贸易")
    second = _customer(client, "乙方实业")
    _order(client, first, api.product_id, 2, 100.0, "2026-03-01")
    _order(client, first, api.product_id, 1, 900.0, "2026-04-15")
    _order(client, second, api.product_id, 5, 60.0, "2026-05-20")

    # 日期闭区间：4-15 当天必须命中
    body = client.get("/api/sales-orders", params={"start_date": "2026-04-15", "end_date": "2026-04-15"}).json()
    assert body["total"] == 1
    assert body["amount_sum"] == 900.0

    # 跨区间 + 客户筛选
    body = client.get(
        "/api/sales-orders",
        params={"start_date": "2026-03-01", "end_date": "2026-05-31", "customer_id": first},
    ).json()
    assert body["total"] == 2

    # 金额区间（下限含等值）
    body = client.get("/api/sales-orders", params={"amount_min": 300, "amount_max": 1000}).json()
    assert body["total"] == 2  # 200 排除，900 与 300（5×60）命中

    # 制单人：admin 建的都在范围里
    body = client.get("/api/sales-orders", params={"created_by": "admin"}).json()
    assert body["total"] == 3
    body = client.get("/api/sales-orders", params={"created_by": "nobody"}).json()
    assert body["total"] == 0


def test_sales_list_sort_whitelist(api):
    client = api.client
    customer = _customer(client, "排序客户")
    _order(client, customer, api.product_id, 1, 50.0, "2026-01-10")
    _order(client, customer, api.product_id, 1, 500.0, "2026-02-10")

    asc = client.get("/api/sales-orders", params={"sort": "amount_asc"}).json()["items"]
    assert [row["total_amount"] for row in asc] == sorted(row["total_amount"] for row in asc)
    desc = client.get("/api/sales-orders", params={"sort": "amount_desc"}).json()["items"]
    assert desc[0]["total_amount"] >= desc[-1]["total_amount"]

    bad = client.get("/api/sales-orders", params={"sort": "drop table"})
    assert bad.status_code == 400
    assert "不支持的排序方式" in bad.json()["detail"]

    bad_date = client.get("/api/sales-orders", params={"start_date": "2026/01/01"})
    assert bad_date.status_code == 400


def test_purchase_list_filters(api):
    client = api.client
    # 采购只能选原料：测试夹具里的商品是成品，这里单独建一个原料
    material = client.post(
        "/api/products",
        json={"sku": "RM-1001", "name": "钢材", "product_type": "raw_material", "cost_price": 10.0},
    )
    assert material.status_code == 200, material.text
    material_id = material.json()["id"]
    for day, price in (("2026-06-01", 100.0), ("2026-07-01", 400.0)):
        response = client.post(
            "/api/purchase-orders",
            json={
                "supplier_id": api.supplier_id,
                "order_date": day,
                "items": [{"product_id": material_id, "quantity": 1, "unit_price": price}],
            },
        )
        assert response.status_code == 200, response.text

    body = client.get("/api/purchase-orders", params={"supplier_id": api.supplier_id}).json()
    assert body["total"] == 2
    body = client.get(
        "/api/purchase-orders", params={"start_date": "2026-07-01", "end_date": "2026-07-31"}
    ).json()
    assert body["total"] == 1
    assert body["amount_sum"] == 400.0


def test_legacy_database_gets_ledger_indexes(tmp_path):
    """老库（表存在但缺索引）升级后必须补上台账索引。"""
    legacy = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with legacy.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE sales_orders (id INTEGER PRIMARY KEY, order_no VARCHAR(40), "
                "customer_id INTEGER, status VARCHAR(20), order_date DATETIME, "
                "total_amount FLOAT, discount_amount FLOAT, remark TEXT, "
                "created_by VARCHAR(50), created_at DATETIME)"
            )
        )

    ensure_schema(legacy)

    names = {row["name"] for row in inspect(legacy).get_indexes("sales_orders")}
    expected = {
        f"ix_sales_orders_{column}"
        for column in ("status", "customer_id", "order_date", "created_by", "created_at")
    }
    assert expected <= names, names
