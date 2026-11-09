"""审批超时提醒/升级 与 盘点生成调整单 的回归测试。"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import update

from app.models import ApprovalLog, StockAdjustment


def _customer(client, name="超时客户") -> int:
    response = client.post("/api/customers", json={"name": name, "level": "B"})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _pending_sales_order(client, api) -> dict:
    created = client.post(
        "/api/sales-orders",
        json={
            "customer_id": _customer(client),
            "items": [{"product_id": api.product_id, "quantity": 1, "unit_price": 100.0}],
        },
    )
    assert created.status_code == 200, created.text
    order_id = created.json()["id"]
    submitted = client.post(f"/api/sales-orders/{order_id}/status", json={"action": "submit"})
    assert submitted.status_code == 200, submitted.text
    return {"id": order_id, "no": created.json()["order_no"]}


def _backdate_submit(api, order_id: int, hours: int = 5) -> None:
    db = api.session()
    try:
        db.execute(
            update(ApprovalLog)
            .where(ApprovalLog.order_type == "sales", ApprovalLog.order_id == order_id)
            .where(ApprovalLog.action == "submit")
            .values(created_at=datetime.now() - timedelta(hours=hours))
        )
        db.commit()
    finally:
        db.close()


def test_sla_config_and_scan_remind(api):
    """超时规则可保存；到点后巡检写提醒日志，待办标记为超时。"""
    client = api.client
    order = _pending_sales_order(client, api)

    saved = client.put(
        "/api/approval-rules/sla/sales",
        json={"remind_hours": 1, "escalate_hours": None, "enabled": True},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["remind_hours"] == 1

    configs = client.get("/api/approval-rules/sla").json()["items"]
    sales_cfg = next(row for row in configs if row["doc_type"] == "sales")
    assert sales_cfg["remind_hours"] == 1 and sales_cfg["enabled"] is True

    # 未超时：巡检不应提醒
    first = client.post("/api/approval-rules/sla/scan").json()
    assert first["reminded"] == 0

    _backdate_submit(api, order["id"], hours=5)
    second = client.post("/api/approval-rules/sla/scan").json()
    assert second["reminded"] >= 1

    # 待办列表带上「已等待/超时」信息
    pending = client.get("/api/approvals/pending").json()["items"]
    row = next(item for item in pending if item["order_id"] == order["id"])
    assert row["overdue"] is True
    assert row["waiting_hours"] >= 5

    logs = client.get(f"/api/approvals/sales/{order['id']}/logs").json()
    assert any(item["action"] == "remind" for item in logs)


def test_sla_scan_escalate(api):
    """两段式：先提醒，超过升级小时再升级（不代签，单据仍待审批）。"""
    client = api.client
    order = _pending_sales_order(client, api)
    client.put(
        "/api/approval-rules/sla/sales",
        json={"remind_hours": 1, "escalate_hours": 5, "enabled": True},
    )
    # 超过提醒小时但未到升级小时：只提醒不升级
    _backdate_submit(api, order["id"], hours=2)
    first = client.post("/api/approval-rules/sla/scan").json()
    assert first["reminded"] >= 1 and first["escalated"] == 0

    # 超过升级小时：升级
    _backdate_submit(api, order["id"], hours=8)
    second = client.post("/api/approval-rules/sla/scan").json()
    assert second["escalated"] >= 1

    logs = client.get(f"/api/approvals/sales/{order['id']}/logs").json()
    assert any(item["action"] == "remind" for item in logs)
    assert any(item["action"] == "escalate" for item in logs)
    detail = client.get(f"/api/sales-orders/{order['id']}").json()
    assert detail["status"] == "pending"  # 超时不等于自动通过


def test_stocktake_generates_adjustment(api):
    """盘点完成自动生成盘盈调整单（作为追溯凭证，不重复改库存）。"""
    client = api.client
    created = client.post("/api/stocktakes", json={"warehouse_id": api.warehouse_id, "remark": "生成调整单"})
    assert created.status_code == 200, created.text
    stocktake_id = created.json()["id"]
    item = created.json()["items"][0]
    stocktake_no = created.json()["stocktake_no"]
    before = item["book_qty"]

    saved = client.post(
        f"/api/stocktakes/{stocktake_id}/items",
        json={"items": [{"item_id": item["id"], "actual_qty": before + 2}]},
    )
    assert saved.status_code == 200, saved.text

    submitted = client.post(f"/api/stocktakes/{stocktake_id}/status", json={"action": "complete"})
    assert submitted.status_code == 200, submitted.text
    data = submitted.json()
    if data["status"] == "pending":
        approved = client.post(f"/api/stocktakes/{stocktake_id}/status", json={"action": "approve"})
        assert approved.status_code == 200, approved.text
        data = approved.json()
    assert data["status"] == "completed"

    adjustments = data["adjustments"]
    assert len(adjustments) == 1
    assert adjustments[0]["adjust_type"] == "gain"
    assert adjustments[0]["source_no"] == stocktake_no

    # 库存只加了一次（调整单不再动库存）
    product = client.get("/api/products", params={"keyword": "SKU-1001"}).json()["items"][0]
    assert product["stock_qty"] == before + 2

    # 重复生成返回同一张单，不重复建单
    again = client.post(f"/api/stocktakes/{stocktake_id}/adjustment").json()["adjustments"]
    assert len(again) == 1 and again[0]["id"] == adjustments[0]["id"]

    db = api.session()
    try:
        docs = db.query(StockAdjustment).filter(StockAdjustment.source_no == stocktake_no).all()
        assert len(docs) == 1 and docs[0].status == "posted"
    finally:
        db.close()
