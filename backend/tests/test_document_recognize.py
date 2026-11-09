"""多单据识别：图片预处理、多页合并、JSON 容错、候选匹配与别名学习。

运行方式（在 backend 目录下）：
    .venv\\Scripts\\python.exe -m pytest tests/test_document_recognize.py -v
"""
from __future__ import annotations

import io
import json

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select

from app.config import settings
from app.models import NameAlias, Product
from app.services import matching, vision


def png(size: tuple[int, int] = (240, 120), color: str = "white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def reply(items: list[dict], **header) -> str:
    return json.dumps({**header, "items": items}, ensure_ascii=False)


# ---------- 1. 图片预处理 ----------

def test_prepare_image_downscales(monkeypatch):
    monkeypatch.setattr(settings, "VISION_MAX_EDGE", 500)
    data, mime, width, height = vision._prepare_image(png((1600, 400)))
    assert mime == "image/jpeg"
    assert max(width, height) <= 500
    assert Image.open(io.BytesIO(data)).size == (width, height)


def test_prepare_image_applies_exif_rotation():
    # EXIF Orientation=6 表示需要顺时针旋转 90°：200×100 应变成 100×200
    exif = Image.Exif()
    exif[0x0112] = 6
    buffer = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(buffer, format="JPEG", exif=exif)
    _data, _mime, width, height = vision._prepare_image(buffer.getvalue())
    assert (width, height) == (100, 200)


def test_prepare_image_rejects_broken_bytes():
    with pytest.raises(HTTPException) as excinfo:
        vision._prepare_image(b"not-an-image")
    assert excinfo.value.status_code == 400
    assert "图片无法读取" in excinfo.value.detail


# ---------- 2. JSON 容错 ----------

@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1,}',
        "{'a': 1}",
        '{“a”: 1}',
        '识别结果：{"a": 1} 以上',
        '```json\n{"a": 1}\n```',
    ],
)
def test_parse_json_repairs_common_model_output(text):
    assert vision._parse_json(text) == {"a": 1}


def test_parse_json_returns_none_for_prose():
    assert vision._parse_json("抱歉，这张照片太模糊了") is None


# ---------- 3. 匹配与候选 ----------

def test_suggest_products_ranks_matching_material(api):
    db = api.session()
    try:
        catalog = matching.load_catalog(db, "raw_material")
        rows = matching.suggest_products(catalog, "不锈钢法兰盘 DN50", spec="DN50")
        assert rows[0]["product_id"] == api.material_id
        # 名称只差一个「盘」、规格又分列时必须给候选，但不能自动落库
        assert 55 <= rows[0]["score"] < matching.AUTO_MATCH_SCORE
        assert matching.pick_best(rows) is None
        assert matching.suggest_products(catalog, "笔记本电池") == []
    finally:
        db.close()


def test_suggest_partner_matches_short_name(api):
    db = api.session()
    try:
        rows = matching.load_partners(db, "supplier")
        candidates = matching.suggest_partners(rows, "上海精工", "supplier")
        assert candidates[0]["id"] == api.supplier_id
        assert matching.pick_best(candidates)["id"] == api.supplier_id
    finally:
        db.close()


def test_learn_alias_is_idempotent(api):
    db = api.session()
    try:
        assert matching.learn_alias(
            db, "product", api.material_id, "法兰盘 50", canonical="不锈钢法兰"
        )
        db.commit()
        # 同一个识别名再记一次只累加命中次数，不新增行
        assert matching.learn_alias(
            db, "product", api.material_id, "法兰盘50", canonical="不锈钢法兰"
        )
        db.commit()
        rows = db.scalars(select(NameAlias).where(NameAlias.entity_type == "product")).all()
        assert len(rows) == 1
        assert rows[0].hits == 2
        # 与正式名称等价的别名没有记录价值
        assert not matching.learn_alias(
            db, "product", api.material_id, "不锈钢法兰", canonical="不锈钢法兰"
        )
    finally:
        db.close()


# ---------- 4. 多页、重试与合计校验 ----------

def test_multi_page_merges_items_and_checks_total(api, vision_stub):
    vision_stub.queue(
        reply(
            [{"sku": "RM-2001", "name": "", "quantity": 2, "unit_price": 10}],
            supplier_name="上海精工",
            receipt_date="2026-03-05",
            total_amount=100,
        )
    )
    vision_stub.queue(
        reply(
            [{"name": "未知新品", "quantity": 1, "unit_price": 5}],
            total_amount=35,
        )
    )
    response = api.client.post(
        "/api/vision/recognize",
        data={"doc_type": "inbound"},
        files=[
            ("files", ("p1.png", png(), "image/png")),
            ("files", ("p2.png", png(), "image/png")),
        ],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["doc_type"] == "inbound"
    assert [item["line_no"] for item in data["items"]] == [1, 2]
    assert data["items"][0]["product_id"] == api.material_id
    assert data["items"][1]["product_id"] is None
    assert len(data["pages"]) == 2
    assert len(data["photo_paths"]) == 2
    assert data["photo_path"] == data["photo_paths"][0]["path"]
    # 合计取最后一页的合计，并与明细比对给出提示
    assert data["total_amount"] == 35
    assert data["total_check"]["ok"] is False
    assert any("相差" in warning for warning in data["warnings"])


def test_retries_once_when_reply_is_not_json(api, vision_stub):
    vision_stub.queue("抱歉，这张图我看不清")
    vision_stub.queue(reply([{"name": "不锈钢法兰", "quantity": 1, "unit_price": 10}]))
    response = api.client.post(
        "/api/vision/recognize",
        data={"doc_type": "inbound"},
        files=[("files", ("p.png", png(), "image/png"))],
    )
    assert response.status_code == 200
    assert len(vision_stub.calls) == 2
    assert response.json()["items"][0]["product_id"] == api.material_id


def test_reports_model_error(api, vision_stub):
    vision_stub.queue_error(401, "invalid api key")
    response = api.client.post(
        "/api/vision/recognize",
        data={"doc_type": "inbound"},
        files=[("files", ("p.png", png(), "image/png"))],
    )
    assert response.status_code == 502
    assert "鉴权失败" in response.json()["detail"]


# ---------- 5. 单据类型与权限 ----------

def test_purchase_recognition_maps_supplier_and_date(api, vision_stub):
    vision_stub.queue(
        reply(
            [{"sku": "RM-2001", "name": "", "quantity": 5, "unit_price": 38.5}],
            supplier_name="上海精工",
            order_date="2026/4/7",
            delivery_date="2026年4月9日",
            doc_no="PO-8899",
            total_amount=192.5,
        )
    )
    response = api.client.post(
        "/api/vision/recognize",
        data={"doc_type": "purchase"},
        files=[("files", ("po.png", png(), "image/png"))],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["doc_type"] == "purchase"
    assert data["supplier_id"] == api.supplier_id
    assert data["order_date"] == "2026-04-07"
    assert data["delivery_date"] == "2026-04-09"
    assert data["doc_no"] == "PO-8899"
    assert data["items"][0]["product_id"] == api.material_id


def test_sales_recognition_maps_customer(api, vision_stub):
    vision_stub.queue(
        reply(
            [{"sku": "SKU-1001", "name": "", "quantity": 2, "unit_price": 150}],
            customer_name="杭州远航",
            order_date="2026-05-01",
            contact="周经理",
            address="杭州市余杭区",
            total_amount=300,
        )
    )
    response = api.client.post(
        "/api/vision/recognize",
        data={"doc_type": "sales"},
        files=[("files", ("so.png", png(), "image/png"))],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["customer_id"] == api.customer_id
    assert data["contact"] == "周经理"
    assert data["items"][0]["product_id"] == api.product_id
    # 销售只匹配成品：原料商品不进入候选
    assert all(item["product_id"] != api.material_id for item in data["items"])


def test_recognition_requires_matching_permission(api):
    response = api.client.post(
        "/api/vision/recognize",
        data={"doc_type": "inbound"},
        files=[("files", ("p.png", png(), "image/png"))],
        headers={"Authorization": f"Bearer {api.sales_token}"},
    )
    assert response.status_code == 403
    assert "inbound:manage" in response.json()["detail"]


def test_unknown_doc_type_rejected(api):
    response = api.client.post(
        "/api/vision/recognize",
        data={"doc_type": "invoice"},
        files=[("files", ("p.png", png(), "image/png"))],
    )
    assert response.status_code == 400
    assert "不支持的单据类型" in response.json()["detail"]


def test_status_lists_doc_types_and_permissions(api):
    data = api.client.get("/api/vision/status").json()
    assert data["enabled"] is True
    assert {item["key"] for item in data["doc_types"]} == {"inbound", "purchase", "sales"}
    assert all(item["allowed"] for item in data["doc_types"])


# ---------- 6. 保存单据：照片留档 + 别名学习 ----------

def test_purchase_order_keeps_photo_and_learns_alias(api, vision_stub):
    vision_stub.queue(
        reply([{"name": "法兰盘 50", "quantity": 2, "unit_price": 40}], supplier_name="上海精工")
    )
    recognized = api.client.post(
        "/api/vision/recognize",
        data={"doc_type": "purchase"},
        files=[("files", ("po.png", png(), "image/png"))],
    ).json()
    item = recognized["items"][0]

    created = api.client.post(
        "/api/purchase-orders",
        json={
            "supplier_id": api.supplier_id,
            "photo_path": recognized["photo_path"],
            "photo_name": recognized["photo_name"],
            "photo_paths": recognized["photo_paths"],
            "ocr_status": recognized["ocr_status"],
            "ocr_raw": recognized["raw_text"],
            "raw_supplier_name": recognized["partner"]["raw_name"],
            "items": [
                {
                    "product_id": api.material_id,
                    "quantity": item["quantity"],
                    "unit_price": item["unit_price"],
                    "raw_name": item["raw_name"],
                }
            ],
        },
    )
    assert created.status_code == 200
    order = created.json()
    assert order["photo_path"] == recognized["photo_path"]
    assert order["photo_paths"][0]["path"] == recognized["photo_path"]
    assert order["ocr_status"] == "recognized"

    detail = api.client.get(f"/api/purchase-orders/{order['id']}").json()
    assert detail["photo_paths"] and detail["ocr_raw"]

    db = api.session()
    try:
        aliases = {row.alias_key: row for row in db.scalars(select(NameAlias)).all()}
        assert matching.product_key(item["raw_name"]) in aliases
        assert matching.partner_key("上海精工") in aliases
    finally:
        db.close()

    # 记住别名后，同样的写法直接命中，不再需要手选
    vision_stub.queue(
        reply([{"name": "法兰盘 50", "quantity": 2, "unit_price": 40}], supplier_name="上海精工")
    )
    again = api.client.post(
        "/api/vision/recognize",
        data={"doc_type": "purchase"},
        files=[("files", ("po.png", png(), "image/png"))],
    ).json()
    assert again["items"][0]["product_id"] == api.material_id
    assert again["items"][0]["match_status"] == "matched"


def test_sales_order_keeps_photo(api):
    created = api.client.post(
        "/api/sales-orders",
        json={
            "customer_id": api.customer_id,
            "remark": "客户订单截图",
            "photo_path": "/uploads/sales/202611/a.jpg",
            "photo_name": "订单截图.jpg",
            "photo_paths": [{"path": "/uploads/sales/202611/a.jpg", "name": "订单截图.jpg"}],
            "ocr_status": "recognized",
            "ocr_raw": '{"customer_name":"杭州远航"}',
            "items": [{"product_id": api.product_id, "quantity": 2, "unit_price": 150}],
        },
    )
    assert created.status_code == 200
    order = created.json()
    assert order["photo_path"] == "/uploads/sales/202611/a.jpg"
    assert order["photo_paths"] == [{"path": "/uploads/sales/202611/a.jpg", "name": "订单截图.jpg"}]
    assert order["ocr_status"] == "recognized"


# ---------- 7. 识别时快速新建商品 ----------

def test_quick_create_product_generates_unique_sku(api):
    first = api.client.post(
        "/api/products/quick", json={"name": "橡胶垫片", "spec": "3mm", "unit": "包"}
    )
    second = api.client.post("/api/products/quick", json={"name": "橡胶垫片 2mm"})
    assert first.status_code == 200
    assert second.status_code == 200
    first_row = first.json()
    assert first_row["sku"].startswith("P")
    assert first_row["sku"] != second.json()["sku"]
    assert first_row["product_type"] == "raw_material"

    db = api.session()
    try:
        product = db.get(Product, first_row["id"])
        assert product.name == "橡胶垫片"
    finally:
        db.close()


def test_quick_create_product_requires_permission(api):
    response = api.client.post(
        "/api/products/quick",
        json={"name": "无权限商品"},
        headers={"Authorization": f"Bearer {api.sales_token}"},
    )
    assert response.status_code == 403
