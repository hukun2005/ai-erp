"""PDF 打印自检：单张、多联、水印、自定义纸张、批量合并、套打校准与打印记录。

用法：cd backend && python scripts/check_print.py
"""
from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import audit
from app.database import Base, get_db
from app.main import app
from app.models import Product, StockBalance, StorageLocation, Supplier, User, Warehouse
from app.security import create_access_token

work = Path(__file__).resolve().parent.parent / "pytest-work"
work.mkdir(exist_ok=True)
db_file = work / "print.db"
if db_file.exists():
    db_file.unlink()

engine = create_engine(f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)
audit.SessionLocal = session


def override_get_db():
    db = session()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

db = session()
admin = User(username="admin", hashed_password="x", full_name="管理员", role="admin", is_active=True)
product = Product(sku="FG-1", name="冲击钻", unit="台", spec="12寸", cost_price=100, sale_price=150, stock_qty=100)
supplier = Supplier(name="供应商甲", contact="王", phone="13800000000")
warehouse = Warehouse(code="MAIN", name="主仓", is_default=True, is_active=True)
db.add_all([admin, product, supplier, warehouse])
db.flush()
location = StorageLocation(warehouse_id=warehouse.id, code="A-01", name="A 区", is_active=True)
db.add(location)
db.flush()
db.add(StockBalance(product_id=product.id, warehouse_id=warehouse.id, location_id=location.id, quantity=100))
db.commit()
token = create_access_token(admin.id, admin.username)
ids = {"product": product.id, "supplier": supplier.id, "warehouse": warehouse.id, "location": location.id}
db.close()

client = TestClient(app, raise_server_exceptions=True)
client.headers.update({"Authorization": f"Bearer {token}"})
failures: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    print(("PASS  " if condition else "FAIL  ") + label + ("" if condition else f"  -> {detail}"))
    if not condition:
        failures.append(label)


def pages(pdf: bytes) -> int:
    return len(PdfReader(BytesIO(pdf)).pages)


def text_of(pdf: bytes, page: int = 0) -> str:
    return PdfReader(BytesIO(pdf)).pages[page].extract_text() or ""


# 准备一张销售订单（含中文、金额、客户信息）
customer = client.post("/api/customers", json={"name": "上海精工", "contact": "李经理", "phone": "13900000000", "address": "上海市浦东新区 1 号"}).json()
order = client.post(
    "/api/sales-orders",
    json={
        "customer_id": customer["id"],
        "items": [
            {"product_id": ids["product"], "quantity": 3, "unit_price": 150.0},
            {"product_id": ids["product"], "quantity": 2, "unit_price": 140.0},
        ],
    },
).json()
client.post(f"/api/sales-orders/{order['id']}/status", json={"action": "submit"})
client.post(f"/api/sales-orders/{order['id']}/status", json={"action": "approve"})

# ── 1. 单张 PDF：内容、中文、金额 ─────────────────────
pdf = client.get(f"/api/print/sales_order/{order['id']}/pdf")
check("单张打印返回 PDF", pdf.status_code == 200 and pdf.content[:4] == b"%PDF", pdf.status_code)
check("单张 1 页", pages(pdf.content) == 1, pages(pdf.content))
body = text_of(pdf.content)
check("PDF 含单号", order["order_no"] in body, body[:120])
check("PDF 含客户与中文商品名", "上海精工" in body and "冲击钻" in body, body[:160])
check("PDF 含金额", "730.00" in body, body[:200])

# ── 2. 多联：每联一页并带联次 ─────────────────────────
multi = client.get(
    f"/api/print/sales_order/{order['id']}/pdf",
    params={"copies": 3, "labels": "存根,客户,财务", "watermark": "内部"},
)
check("多联返回 3 页", pages(multi.content) == 3, pages(multi.content))
first = text_of(multi.content, 0)
second = text_of(multi.content, 1)
check("第一联标注存根", "存根" in first, first[:120])
check("第二联标注客户", "客户" in second, second[:120])
check("水印文字写入", "内部" in first, first[:200])

# ── 3. 自定义纸张（241×140 针式）= ────────────────────
custom = client.get(
    f"/api/print/sales_order/{order['id']}/pdf",
    params={"width_mm": 241, "height_mm": 140, "offset_x_mm": 1.5, "offset_y_mm": -2},
)
check("自定义纸张可打印", custom.status_code == 200 and pages(custom.content) >= 1, custom.status_code)
box = PdfReader(BytesIO(custom.content)).pages[0].mediabox
check("纸张尺寸 241×140mm", abs(float(box.width) - 241 * 72 / 25.4) < 2 and abs(float(box.height) - 140 * 72 / 25.4) < 2, (float(box.width), float(box.height)))

# ── 4. 批量合并 ─────────────────────────────────────
order2 = client.post(
    "/api/sales-orders",
    json={"customer_id": customer["id"], "items": [{"product_id": ids["product"], "quantity": 1, "unit_price": 150.0}]},
).json()
batch = client.post(
    "/api/print/batch",
    json={"doc_type": "sales_order", "ids": [order["id"], order2["id"]], "copies": 1},
)
check("批量合并返回 PDF", batch.status_code == 200 and batch.content[:4] == b"%PDF", batch.status_code)
check("合并后 2 页", pages(batch.content) == 2, pages(batch.content))
check("合并含两张单号", order["order_no"] in text_of(batch.content, 0) and order2["order_no"] in text_of(batch.content, 1), "")

over = client.post("/api/print/batch", json={"doc_type": "sales_order", "ids": [0]})
check("空选择被拒", over.status_code == 400, over.status_code)

# ── 5. 采购单 / 入库单打印（覆盖其它单据类型） ─────────
material = client.post("/api/products", json={"sku": "RM-1", "name": "钢材", "product_type": "raw_material", "cost_price": 10}).json()
po = client.post(
    "/api/purchase-orders",
    json={"supplier_id": ids["supplier"], "items": [{"product_id": material["id"], "quantity": 10, "unit_price": 12.0}]},
).json()
po_pdf = client.get(f"/api/print/purchase_order/{po['id']}/pdf")
check("采购单可打印", po_pdf.status_code == 200 and "采购订单" in text_of(po_pdf.content), po_pdf.status_code)

inbound = client.post(
    "/api/inbounds",
    json={
        "supplier_id": ids["supplier"],
        "warehouse_id": ids["warehouse"],
        "items": [{"product_id": material["id"], "quantity": 10, "unit_price": 12.0, "batch_no": "B-1"}],
    },
).json()
in_pdf = client.get(f"/api/print/inbound/{inbound['id']}/pdf")
check("入库单可打印且带批次列", in_pdf.status_code == 200 and "批次" in text_of(in_pdf.content), in_pdf.status_code)

# ── 6. 套打校准页 ───────────────────────────────────
cal = client.get("/api/print/calibration/sheet", params={"offset_x_mm": 2, "offset_y_mm": -1})
check("校准页返回 PDF", cal.status_code == 200 and cal.content[:4] == b"%PDF", cal.status_code)
check("校准页含偏移信息", "X=2.0mm" in text_of(cal.content), text_of(cal.content)[:120])

# ── 7. 打印记录 ─────────────────────────────────────
jobs = client.get("/api/print/jobs").json()
check("打印记录已写入", len(jobs) >= 4, len(jobs))
check("批量为 batch 类型", any(row["kind"] == "batch" and row["page_count"] == 2 for row in jobs), jobs[:2])
check("多联记录份数", any(row["copies"] == 3 for row in jobs), [row["copies"] for row in jobs])

# ── 8. 权限：仓管不能打销售单 ─────────────────────────
from app.models import User as UserModel

db = session()
keeper = UserModel(username="keeper", hashed_password="x", full_name="仓管", role="warehouse", is_active=True)
db.add(keeper)
db.commit()
db.refresh(keeper)
keeper_token = create_access_token(keeper.id, keeper.username)
db.close()
denied = client.get(
    f"/api/print/sales_order/{order['id']}/pdf",
    headers={"Authorization": f"Bearer {keeper_token}"},
)
check("无权限角色被拒", denied.status_code == 403, denied.status_code)

app.dependency_overrides.clear()
print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
