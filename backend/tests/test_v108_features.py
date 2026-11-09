"""1.0.8 新功能回归：期末结账、价税分离、银行对账、通用审批条件、商品条码。

运行方式（在 backend 目录下）：
    .venv\\Scripts\\python.exe -m pytest tests/test_v108_features.py -v
"""
from __future__ import annotations

import io
from datetime import datetime

from openpyxl import Workbook

from app.models import Payment, Voucher
from app.security import create_access_token


def _period() -> str:
    return datetime.now().strftime("%Y-%m")


def _seed_accounts(api) -> None:
    """测试库没有走应用启动流程，会计科目需要显式初始化。"""
    from app.services import gl

    db = api.session()
    try:
        gl.seed_accounts(db)
    finally:
        db.close()


def _finance_token(api) -> str:
    from app.models import User

    db = api.session()
    try:
        user = User(username="fin02", hashed_password="x", full_name="财务", role="finance", is_active=True)
        db.add(user)
        db.commit()
        db.refresh(user)
        return create_access_token(user.id, user.username)
    finally:
        db.close()


def _customer(client, name: str, level: str = "B", credit_limit: float = 0) -> int:
    response = client.post(
        "/api/customers", json={"name": name, "level": level, "credit_limit": credit_limit}
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _order(client, api, customer_id: int, quantity: int = 1, price: float = 150.0, tax_rate: float = 0) -> int:
    response = client.post(
        "/api/sales-orders",
        json={
            "customer_id": customer_id,
            "tax_rate": tax_rate,
            "items": [{"product_id": api.product_id, "quantity": quantity, "unit_price": price}],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _confirm(client, order_id: int) -> None:
    assert client.post(f"/api/sales-orders/{order_id}/status", json={"action": "submit"}).status_code == 200
    approved = client.post(f"/api/sales-orders/{order_id}/status", json={"action": "approve"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "confirmed"


def _rule(client, **kwargs):
    payload = {
        "doc_type": kwargs.get("doc_type", "sales"),
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


# ---------------- 1. 期末结账 / 期间锁 ----------------
def test_period_close_locks_vouchers_and_reopen(api):
    client = api.client
    period = _period()
    _seed_accounts(api)

    closed = client.post("/api/period/close", json={"period": period})
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"

    # 已结账期间不能再记手工凭证
    blocked = client.post(
        "/api/ledger/vouchers",
        json={
            "voucher_date": datetime.now().isoformat(),
            "summary": "结账后补录",
            "lines": [
                {"account_code": "1001", "debit": 100, "credit": 0},
                {"account_code": "1002", "debit": 0, "credit": 100},
            ],
        },
    )
    assert blocked.status_code == 400, blocked.text
    assert "已结账" in blocked.text

    # 自动生成凭证同样被拒绝
    gen = client.post("/api/ledger/vouchers/generate", params={"period": period})
    assert gen.status_code == 400, gen.text

    reopened = client.post("/api/period/reopen", json={"period": period})
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "open"

    ok = client.post(
        "/api/ledger/vouchers",
        json={
            "voucher_date": datetime.now().isoformat(),
            "summary": "反结账后补录",
            "lines": [
                {"account_code": "1001", "debit": 100, "credit": 0},
                {"account_code": "1002", "debit": 0, "credit": 100},
            ],
        },
    )
    assert ok.status_code == 200, ok.text


def test_period_close_carries_profit_and_reopen_voids_it(api):
    client = api.client
    period = _period()
    _seed_accounts(api)
    customer = _customer(client, "结转客户")
    order = _order(client, api, customer, quantity=1, price=300.0)
    _confirm(client, order)

    generated = client.post("/api/ledger/vouchers/generate", params={"period": period})
    assert generated.status_code == 200, generated.text

    closed = client.post("/api/period/close", json={"period": period})
    assert closed.status_code == 200, closed.text
    carry_no = closed.json()["carry_voucher_no"]
    assert carry_no, "有损益发生时结账必须生成结转损益凭证"

    db = api.session()
    try:
        carry = db.query(Voucher).filter(Voucher.voucher_no == carry_no).first()
        assert carry is not None and carry.source_type == "closing"
        from app.models import VoucherLine

        carry_codes = {
            row.account_code for row in db.query(VoucherLine).filter(VoucherLine.voucher_id == carry.id).all()
        }
        assert "3103" in carry_codes  # 结转损益必须进本年利润
    finally:
        db.close()

    # 利润表净利 = 300 - 150（成本价 100 × ? 这里按 avg_cost/cost_price 计算成本）
    income = client.get("/api/ledger/statements/income-statement", params={"period": period}).json()
    assert income["revenue"] == 300.0

    reopened = client.post("/api/period/reopen", json={"period": period})
    assert reopened.status_code == 200, reopened.text

    db = api.session()
    try:
        carry = db.query(Voucher).filter(Voucher.voucher_no == carry_no).first()
        assert carry.status == "voided"
    finally:
        db.close()


# ---------------- 2. 价税分离 / 税务报表 ----------------
def test_tax_split_in_generated_vouchers(api):
    client = api.client
    period = _period()
    customer = _customer(client, "含税客户")
    # 1 × 1130 = 1130（价税合计），税率 13% → 不含税 1000、税 130
    order = _order(client, api, customer, quantity=1, price=1130.0, tax_rate=13)
    _confirm(client, order)

    generated = client.post("/api/ledger/vouchers/generate", params={"period": period})
    assert generated.status_code == 200, generated.text

    vouchers = client.get("/api/ledger/vouchers", params={"period": period}).json()
    revenue_voucher = next(
        v for v in vouchers if v["source_type"] == "sales" and "确认收入" in v["summary"]
    )
    amounts = {ln["account_code"]: (ln["debit"], ln["credit"]) for ln in revenue_voucher["lines"]}
    assert amounts["1122"][0] == 1130.0      # 应收 = 价税合计
    assert amounts["5001"][1] == 1000.0      # 收入 = 不含税
    assert amounts["2221"][1] == 130.0       # 销项税额


def test_tax_report_from_invoices(api):
    client = api.client
    now = datetime.now().isoformat()
    sales = client.post(
        "/api/invoices",
        json={
            "invoice_no": "INV-S-1", "direction": "sales", "partner_name": "客户",
            "amount": 1000, "tax_rate": 13, "tax_amount": 130, "total_amount": 1130,
            "issue_date": now,
        },
    )
    assert sales.status_code == 200, sales.text
    purchase = client.post(
        "/api/invoices",
        json={
            "invoice_no": "INV-P-1", "direction": "purchase", "partner_name": "供应商",
            "amount": 500, "tax_rate": 13, "tax_amount": 65, "total_amount": 565,
            "issue_date": now,
        },
    )
    assert purchase.status_code == 200, purchase.text
    purchase_id = purchase.json()["id"]
    assert client.post(f"/api/invoices/{purchase_id}/status", json={"action": "certify"}).status_code == 200

    report = client.get("/api/tax/report", params={"period": _period()}).json()
    assert report["sales_tax"] == 130.0
    assert report["purchase_tax"] == 65.0
    assert report["certified_purchase_tax"] == 65.0
    assert report["tax_payable"] == 65.0   # 销项 130 - 可抵扣进项 65

    detail = client.get("/api/tax/detail", params={"period": _period()}).json()
    assert detail["total"] == 2 and len(detail["items"]) == 2


# ---------------- 3. 银行流水导入 + 自动对账 ----------------
def _bank_xlsx(rows: list[list]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["交易日期", "方向", "金额", "对方户名", "流水号", "摘要", "银行账号"])
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_bank_import_and_apply_match(api):
    client = api.client
    customer = _customer(client, "流水客户")
    order = _order(client, api, customer, quantity=2, price=300.0)  # 600
    _confirm(client, order)

    content = _bank_xlsx(
        [[datetime.now().strftime("%Y-%m-%d"), "收入", 600.0, "流水客户", "BANK001", "货款", "6222"]]
    )
    imported = client.post(
        "/api/bank/import",
        files={"file": ("bank.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["created"] == 1

    statements = client.get("/api/bank/statements").json()
    assert statements["total"] == 1
    assert statements["items"][0]["counterparty"] == "流水客户"

    matches = client.get("/api/bank/matches", params={"status": "suggested"}).json()
    assert len(matches) == 1
    match = matches[0]
    assert match["partner_type"] == "customer"
    assert match["confidence"] == "high"
    assert match["amount"] == 600.0

    applied = client.post(f"/api/bank/matches/{match['id']}/apply")
    assert applied.status_code == 200, applied.text
    assert applied.json()["partner_name"] == "流水客户"

    # 收款已生成并核销，订单余额应为 0
    statements_after = client.get("/api/bank/statements").json()
    assert statements_after["items"][0]["status"] == "matched"
    detail = client.get(f"/api/sales-orders/{order}").json()
    assert detail["status"] == "confirmed"

    db = api.session()
    try:
        payment = db.query(Payment).order_by(Payment.id.desc()).first()
        assert payment is not None and payment.amount == 600.0
        assert payment.allocations and payment.allocations[0].order_id == order
    finally:
        db.close()


# ---------------- 4. 通用审批：任意单据类型 + 条件分支 ----------------
def test_approval_condition_customer_level(api):
    """条件分支：只有 A 级客户命中两级规则，B 级客户退回单级。"""
    client = api.client
    _rule(client, name="A级一级", level=1, conditions={"customer_level": ["A"]})
    _rule(client, name="A级二级", level=2, conditions={"customer_level": ["A"]})

    a_customer = _customer(client, "A级客户", level="A")
    a_order = _order(client, api, a_customer)
    submitted = client.post(f"/api/sales-orders/{a_order}/status", json={"action": "submit"}).json()
    assert submitted["approval_required"] == 2

    b_customer = _customer(client, "B级客户", level="B")
    b_order = _order(client, api, b_customer)
    b_submitted = client.post(f"/api/sales-orders/{b_order}/status", json={"action": "submit"}).json()
    assert b_submitted["approval_required"] == 1


def test_requisition_multi_level_approval(api):
    """采购申请接入通用审批：两级规则 + 待办列表。"""
    client = api.client
    _rule(client, doc_type="requisition", name="小额", min_amount=0, max_amount=1000, level=1, permission="requisition:approve")
    _rule(client, doc_type="requisition", name="大额一级", min_amount=1000, max_amount=None, level=1, permission="requisition:approve")
    _rule(client, doc_type="requisition", name="大额二级", min_amount=1000, max_amount=None, level=2, permission="requisition:approve")

    created = client.post(
        "/api/purchase-requisitions",
        json={"items": [{"product_id": api.material_id, "quantity": 100, "est_price": 20.0}]},
    )
    assert created.status_code == 200, created.text
    req_id = created.json()["id"]

    submitted = client.post(f"/api/purchase-requisitions/{req_id}/status", json={"action": "submit"})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["approval_required"] == 2

    pending = client.get("/api/approvals/pending").json()
    assert any(row["order_type"] == "requisition" and row["order_id"] == req_id for row in pending["items"])

    first = client.post(f"/api/purchase-requisitions/{req_id}/status", json={"action": "approve"})
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "pending"

    second = client.post(f"/api/purchase-requisitions/{req_id}/status", json={"action": "approve"})
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "approved"


# ---------------- 5. 商品条码 + 扫码 ----------------
def test_product_barcode_and_scan(api):
    client = api.client
    created = client.post(
        "/api/products",
        json={"sku": "SKU-BC-1", "name": "扫码商品", "barcode": "6901234567890", "product_type": "raw_material"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["barcode"] == "6901234567890"

    # 条码重复建档被拒绝
    dup = client.post(
        "/api/products",
        json={"sku": "SKU-BC-2", "name": "重复条码", "barcode": "6901234567890"},
    )
    assert dup.status_code == 400, dup.text

    by_barcode = client.get("/api/products/scan", params={"code": "6901234567890"})
    assert by_barcode.status_code == 200, by_barcode.text
    assert by_barcode.json()["sku"] == "SKU-BC-1"

    # 兜底：直接扫/输入 SKU
    by_sku = client.get("/api/products/scan", params={"code": "SKU-1001"})
    assert by_sku.status_code == 200, by_sku.text
    assert by_sku.json()["sku"] == "SKU-1001"

    missing = client.get("/api/products/scan", params={"code": "NOPE-000"})
    assert missing.status_code == 404
