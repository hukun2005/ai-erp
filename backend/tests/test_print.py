"""服务端 PDF 打印：单张、多联水印、批量合并、打印记录。

用 pypdf 校验页数，并抽取文本层确认中文与金额真的写进了 PDF。
"""
from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader


def _order(client, api, quantity: int = 2) -> dict:
    customer = client.post("/api/customers", json={"name": "打印客户", "contact": "李经理"}).json()
    order = client.post(
        "/api/sales-orders",
        json={
            "customer_id": customer["id"],
            "items": [{"product_id": api.product_id, "quantity": quantity, "unit_price": 150.0}],
        },
    ).json()
    client.post(f"/api/sales-orders/{order['id']}/status", json={"action": "submit"})
    client.post(f"/api/sales-orders/{order['id']}/status", json={"action": "approve"})
    return order


def _pages(pdf: bytes) -> int:
    return len(PdfReader(BytesIO(pdf)).pages)


def _text(pdf: bytes, page: int = 0) -> str:
    return PdfReader(BytesIO(pdf)).pages[page].extract_text() or ""


def test_single_pdf_contains_document_data(api):
    client = api.client
    order = _order(client, api)
    response = client.get(f"/api/print/sales_order/{order['id']}/pdf")
    assert response.status_code == 200, response.text
    assert response.content[:4] == b"%PDF"
    assert _pages(response.content) == 1
    body = _text(response.content)
    assert order["order_no"] in body
    assert "打印客户" in body
    assert "冲击钻" in body
    assert "300.00" in body


def test_multi_copy_with_labels_and_watermark(api):
    client = api.client
    order = _order(client, api)
    response = client.get(
        f"/api/print/sales_order/{order['id']}/pdf",
        params={"copies": 3, "labels": "存根,客户,财务", "watermark": "内部"},
    )
    assert response.status_code == 200, response.text
    assert _pages(response.content) == 3
    assert "存根" in _text(response.content, 0)
    assert "客户" in _text(response.content, 1)
    assert "内部" in _text(response.content, 0)


def test_custom_paper_size(api):
    client = api.client
    order = _order(client, api)
    response = client.get(
        f"/api/print/sales_order/{order['id']}/pdf",
        params={"width_mm": 241, "height_mm": 140},
    )
    assert response.status_code == 200, response.text
    box = PdfReader(BytesIO(response.content)).pages[0].mediabox
    assert abs(float(box.width) - 241 * 72 / 25.4) < 2
    assert abs(float(box.height) - 140 * 72 / 25.4) < 2


def test_batch_merge_and_job_log(api):
    client = api.client
    first = _order(client, api, 1)
    second = _order(client, api, 2)
    response = client.post(
        "/api/print/batch",
        json={"doc_type": "sales_order", "ids": [first["id"], second["id"]]},
    )
    assert response.status_code == 200, response.text
    assert _pages(response.content) == 2
    assert first["order_no"] in _text(response.content, 0)
    assert second["order_no"] in _text(response.content, 1)

    jobs = client.get("/api/print/jobs").json()
    assert any(row["kind"] == "batch" and row["page_count"] == 2 for row in jobs)

    empty = client.post("/api/print/batch", json={"doc_type": "sales_order", "ids": []})
    assert empty.status_code == 400


def test_calibration_sheet(api):
    client = api.client
    response = client.get("/api/print/calibration/sheet", params={"offset_x_mm": 2, "offset_y_mm": -1})
    assert response.status_code == 200
    assert response.content[:4] == b"%PDF"
    assert "X=2.0mm" in _text(response.content)
