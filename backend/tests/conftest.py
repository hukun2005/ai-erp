"""识别相关测试夹具。

每个测试使用独立的临时 SQLite 库与临时上传目录，避免污染开发库（erp.db）
和 backend/uploads；测试结束后依赖覆盖与配置补丁自动还原。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import audit
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import Customer, Product, StockBalance, StorageLocation, Supplier, User, Warehouse
from app.security import create_access_token
from app.services import vision


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """返回已登录客户端、测试数据 id 与临时上传目录。"""
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # 审计日志默认写开发库，测试中同样指向临时库
    monkeypatch.setattr(audit, "SessionLocal", testing_session)
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(settings, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(settings, "VISION_API_KEY", "test-vision-key", raising=False)

    db = testing_session()
    admin = User(username="admin", hashed_password="x", full_name="管理员", role="admin", is_active=True)
    sales = User(username="sales", hashed_password="x", full_name="销售员", role="sales", is_active=True)
    supplier = Supplier(name="上海精工机械有限公司", contact="王经理", phone="13800000000")
    customer = Customer(name="杭州远航机电有限公司", contact="周经理", phone="13900000000", level="A")
    product = Product(
        sku="SKU-1001", name="冲击钻", category="电动工具", unit="台",
        cost_price=100.0, sale_price=150.0, stock_qty=5, safety_stock=2,
    )
    # 入库/采购只认原料：识别匹配按物料类型过滤，另建一个原料商品供入库用例使用
    material = Product(
        sku="RM-2001", name="不锈钢法兰", category="五金", product_type="raw_material",
        unit="个", cost_price=38.5, sale_price=52.0, stock_qty=0, safety_stock=0,
    )
    warehouse = Warehouse(code="MAIN", name="主仓", is_default=True, is_active=True)
    db.add_all([admin, sales, supplier, customer, product, material, warehouse])
    db.flush()
    location = StorageLocation(warehouse_id=warehouse.id, code="A-01", name="A 区", is_active=True)
    db.add(location)
    db.flush()
    db.add(
        StockBalance(
            product_id=product.id, warehouse_id=warehouse.id,
            location_id=location.id, quantity=5,
        )
    )
    db.commit()

    ctx = SimpleNamespace(
        client=TestClient(app, raise_server_exceptions=True),
        session=testing_session,
        upload_dir=upload_dir,
        admin_token=create_access_token(admin.id, admin.username),
        sales_token=create_access_token(sales.id, sales.username),
        supplier_id=supplier.id,
        customer_id=customer.id,
        product_id=product.id,
        material_id=material.id,
        warehouse_id=warehouse.id,
        location_id=location.id,
    )
    ctx.client.headers.update({"Authorization": f"Bearer {ctx.admin_token}"})
    db.close()

    yield ctx

    app.dependency_overrides.pop(get_db, None)


class VisionStub:
    """视觉模型替身：记录每次请求，按顺序返回预设回复（用完后重复最后一条）。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.replies: list[tuple[int, dict | None, str]] = []

    def queue(self, content: str) -> None:
        """排入一条正常的模型回复。"""
        self.replies.append((200, {"choices": [{"message": {"content": content}}]}, ""))

    def queue_error(self, status_code: int, text: str) -> None:
        """排入一次失败响应（用于鉴权/限流等分支）。"""
        self.replies.append((status_code, None, text))

    def next_reply(self) -> tuple[int, dict | None, str]:
        if not self.replies:
            return (200, {"choices": [{"message": {"content": "{}"}}]}, "")
        if len(self.replies) == 1:
            return self.replies[0]
        return self.replies.pop(0)


@pytest.fixture()
def vision_stub(monkeypatch):
    """把 vision.httpx 换成本地替身，测试不访问真实模型。"""
    stub = VisionStub()

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict | None, text: str):
            self.status_code = status_code
            self._payload = payload
            self.text = text

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                request = httpx.Request("POST", "https://example.test/chat/completions")
                raise httpx.HTTPStatusError(
                    "error",
                    request=request,
                    response=httpx.Response(self.status_code, request=request, text=self.text),
                )

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, headers=None, json=None):
            stub.calls.append({"url": url, "headers": headers, "json": json})
            status_code, payload, text = stub.next_reply()
            return FakeResponse(status_code, payload, text)

    monkeypatch.setattr(
        vision,
        "httpx",
        SimpleNamespace(
            Client=FakeClient,
            HTTPStatusError=httpx.HTTPStatusError,
            HTTPError=httpx.HTTPError,
        ),
    )
    return stub


def reply(items: list[dict], **header) -> str:
    """拼一条模型回复，减少用例里的 JSON 噪音。"""
    return json.dumps({**header, "items": items}, ensure_ascii=False)
