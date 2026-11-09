"""入库单照片识别测试用例。

覆盖四层：
1. 模型输出解析（JSON 清洗、日期归一、数字容错）；
2. 主数据匹配（SKU / 精确名称 / 模糊名称 / 未匹配）；
3. 识别接口校验（未配 Key、文件类型、空文件、超大文件、权限）；
4. 端到端（模拟视觉模型返回 → 自动回填 → 保存草稿 → 确认入库加库存 → 取消回滚）。

运行方式（在 backend 目录下）：
    .venv\\Scripts\\python.exe -m pytest tests/test_inbound_recognize.py -v
"""
from __future__ import annotations

import io
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from app.models import Product
from app.services import vision


# ---------- 视觉模型替身 ----------

class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
            raise httpx.HTTPStatusError(
                "error", request=request,
                response=httpx.Response(self.status_code, request=request, text=self.text),
            )


def fake_vision(payload=None, status_code=200, text=""):
    """构造 httpx 替身，记录实际发送的请求体用于断言。"""
    calls: list[dict] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, headers=None, json=None):
            calls.append({"url": url, "headers": headers, "json": json})
            return FakeResponse(payload, status_code, text)

    return SimpleNamespace(
        Client=FakeClient,
        HTTPStatusError=httpx.HTTPStatusError,
        HTTPError=httpx.HTTPError,
        calls=calls,
    )


def patch_vision(monkeypatch, payload=None, status_code=200, text=""):
    fake = fake_vision(payload, status_code, text)
    monkeypatch.setattr(vision, "httpx", fake)
    return fake


GOOD_REPLY = """识别结果如下：
```json
{"supplier_name":"上海精工","receipt_date":"2026年3月5日","total_amount":"1,200.50",
 "items":[
   {"sku":"RM-2001","name":"","quantity":2,"unit_price":100},
   {"sku":"","name":"不锈钢法兰","quantity":0,"unit_price":0},
   {"sku":"","name":"未知新品","quantity":3,"unit_price":10},
   {"sku":"","name":"","quantity":0,"unit_price":0}
 ]}
```
"""

def make_png(size: tuple[int, int] = (160, 90)) -> bytes:
    """真实可解码的 PNG：识别前会先本地转正压缩，伪造字节会被当成坏图片拒绝。"""
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


PNG_BYTES = make_png()


# ---------- 1. 模型输出解析 ----------

@pytest.mark.parametrize(
    "text, expected",
    [
        ('{"a":1}', {"a": 1}),
        ('```json\n{"a":1}\n```', {"a": 1}),
        ('好的，结果如下：{"a":1} 以上', {"a": 1}),
        ("[1,2,3]", None),
        ("抱歉，这张图看不清", None),
        ("", None),
    ],
)
def test_parse_json(text, expected):
    assert vision._parse_json(text) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-03-15", "2026-03-15"),
        ("2026年3月5日", "2026-03-05"),
        ("2026/3/5", "2026-03-05"),
        ("2026.3.5", "2026-03-05"),
        ("3月5日", ""),
        ("", ""),
    ],
)
def test_normalize_date(raw, expected):
    assert vision._normalize_date(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("1,200", 1200), ("3.7", 3), ("-2", -2), (None, 0), ("abc", 0), ("", 0)],
)
def test_to_int(raw, expected):
    assert vision._to_int(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("1,200.50", 1200.5), ("100", 100.0), ("abc", 0.0), (None, 0.0)],
)
def test_to_float(raw, expected):
    assert vision._to_float(raw) == expected


# ---------- 2. 主数据匹配 ----------

def test_match_product_by_sku_and_name(api):
    db = api.session()
    try:
        assert vision._match_product(db, "sku-1001", "").id == api.product_id  # SKU 忽略大小写
        assert vision._match_product(db, "", "冲击钻").id == api.product_id
        assert vision._match_product(db, "", "冲击").id == api.product_id  # 模糊名称
        assert vision._match_product(db, "SKU-9999", "不存在的商品") is None
    finally:
        db.close()


def test_match_supplier(api):
    db = api.session()
    try:
        assert vision._match_supplier(db, "上海精工机械有限公司").id == api.supplier_id
        assert vision._match_supplier(db, "上海精工").id == api.supplier_id
        assert vision._match_supplier(db, "") is None
    finally:
        db.close()


# ---------- 3. 接口输入校验 ----------

def test_recognize_requires_key(api, monkeypatch):
    monkeypatch.setattr(vision.settings, "VISION_API_KEY", "")
    r = api.client.post("/api/inbounds/recognize", files={"file": ("a.png", PNG_BYTES, "image/png")})
    assert r.status_code == 400
    assert "未配置视觉模型 Key" in r.json()["detail"]


def test_recognize_rejects_unsupported_file(api):
    r = api.client.post("/api/inbounds/recognize", files={"file": ("a.pdf", b"pdf", "application/pdf")})
    assert r.status_code == 400
    assert "仅支持" in r.json()["detail"]


def test_recognize_rejects_empty_file(api):
    r = api.client.post("/api/inbounds/recognize", files={"file": ("a.png", b"", "image/png")})
    assert r.status_code == 400
    assert "图片内容为空" in r.json()["detail"]


def test_recognize_rejects_oversize_file(api, monkeypatch):
    monkeypatch.setattr(vision.settings, "MAX_UPLOAD_MB", 1)
    big = b"0" * (2 * 1024 * 1024)
    r = api.client.post("/api/inbounds/recognize", files={"file": ("a.png", big, "image/png")})
    assert r.status_code == 400
    assert "不能超过" in r.json()["detail"]


def test_recognize_requires_inbound_manage(api):
    r = api.client.post(
        "/api/inbounds/recognize",
        headers={"Authorization": f"Bearer {api.sales_token}"},
        files={"file": ("a.png", PNG_BYTES, "image/png")},
    )
    assert r.status_code == 403

    r = api.client.post(
        "/api/inbounds/recognize",
        headers={"Authorization": ""},
        files={"file": ("a.png", PNG_BYTES, "image/png")},
    )
    assert r.status_code == 401


# ---------- 4. 识别成功与模型异常 ----------

def test_recognize_matches_master_data(api, monkeypatch):
    fake = patch_vision(
        monkeypatch,
        payload={"choices": [{"message": {"content": GOOD_REPLY}}]},
    )
    r = api.client.post("/api/inbounds/recognize", files={"file": ("单据.png", PNG_BYTES, "image/png")})
    assert r.status_code == 200
    data = r.json()

    # 供应商与日期归一
    assert data["supplier_id"] == api.supplier_id
    assert data["supplier_name"] == "上海精工机械有限公司"
    assert data["receipt_date"] == "2026-03-05"
    assert data["total_amount"] == 1200.5

    # 明细匹配：SKU 行 + 名称行 + 未匹配行，空行被丢弃
    assert len(data["items"]) == 3
    first, second, third = data["items"]
    assert first["product_id"] == api.material_id
    assert second["product_id"] == api.material_id
    assert second["quantity"] == 1  # 数量 0 兜底为 1
    assert third["product_id"] is None
    assert third["product_name"] == "未知新品"

    # 照片落盘与访问路径
    assert data["photo_path"].startswith("/uploads/inbound/")
    saved = api.upload_dir / data["photo_path"].removeprefix("/uploads/")
    assert saved.is_file()
    assert data["ocr_status"] == "recognized"
    assert data["raw_text"]

    # 请求体：图片以 data URI 传给视觉模型
    assert len(fake.calls) == 1
    sent = fake.calls[0]["json"]
    assert sent["model"] == vision.settings.VISION_MODEL
    image = sent["messages"][0]["content"][0]["image_url"]["url"]
    # 上传前会按 EXIF 转正并压缩成 JPEG，手机原图不会直接发给模型
    assert image.startswith("data:image/jpeg;base64,")
    assert fake.calls[0]["headers"]["Authorization"].startswith("Bearer ")


def test_recognize_reports_unreadable_image(api, monkeypatch):
    patch_vision(monkeypatch, payload={"choices": [{"message": {"content": "抱歉，看不清"}}]})
    r = api.client.post("/api/inbounds/recognize", files={"file": ("a.png", PNG_BYTES, "image/png")})
    assert r.status_code == 502
    assert "未能从图片中识别出有效单据" in r.json()["detail"]


def test_recognize_reports_model_error(api, monkeypatch):
    patch_vision(monkeypatch, status_code=401, text="invalid api key")
    r = api.client.post("/api/inbounds/recognize", files={"file": ("a.png", PNG_BYTES, "image/png")})
    assert r.status_code == 502
    assert "视觉模型调用失败" in r.json()["detail"]


# ---------- 5. 端到端：识别结果 → 草稿 → 入库 → 回滚 ----------

def test_recognized_photo_flows_to_inbound(api, monkeypatch):
    patch_vision(monkeypatch, payload={"choices": [{"message": {"content": GOOD_REPLY}}]})
    rec = api.client.post(
        "/api/inbounds/recognize", files={"file": ("单据.png", PNG_BYTES, "image/png")}
    ).json()
    assert rec["items"][0]["product_id"] == api.material_id

    created = api.client.post(
        "/api/inbounds",
        json={
            "supplier_id": rec["supplier_id"],
            "warehouse_id": api.warehouse_id,
            "receipt_date": rec["receipt_date"],
            "remark": "照片识别自动建单",
            "photo_path": rec["photo_path"],
            "photo_name": rec["photo_name"],
            "ocr_status": rec["ocr_status"],
            "ocr_raw": rec["raw_text"],
            "items": [
                {
                    "product_id": rec["items"][0]["product_id"],
                    "quantity": rec["items"][0]["quantity"],
                    "unit_price": rec["items"][0]["unit_price"],
                }
            ],
        },
    )
    assert created.status_code == 200
    order = created.json()
    assert order["status"] == "draft"
    assert order["photo_path"] == rec["photo_path"]
    assert order["total_amount"] == 200.0
    assert (api.upload_dir / order["photo_path"].removeprefix("/uploads/")).is_file()

    def stock():
        db = api.session()
        try:
            return db.get(Product, api.material_id).stock_qty
        finally:
            db.close()

    assert stock() == 0
    confirmed = api.client.post(f"/api/inbounds/{order['id']}/status", json={"action": "confirm"})
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert stock() == 2

    cancelled = api.client.post(f"/api/inbounds/{order['id']}/status", json={"action": "cancel"})
    assert cancelled.status_code == 200
    assert stock() == 0
