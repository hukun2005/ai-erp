"""对账闭环自检：收款 → 核销 → 对账单 → 公开确认 → 作废回退。

用临时库副本运行，不触碰业务数据。

用法（在 backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\check_receivables.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_source_db = BASE_DIR / "erp.db"
_temp_db = Path(tempfile.gettempdir()) / "erp_check_ar.db"
for suffix in ("", "-wal", "-shm"):
    stale = Path(str(_temp_db) + suffix)
    if stale.exists():
        stale.unlink()
if _source_db.exists():
    shutil.copy(_source_db, _temp_db)
os.environ["DATABASE_URL"] = f"sqlite:///{_temp_db.as_posix()}"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

failures = 0


def check(name: str, passed: bool, detail: str = "") -> None:
    global failures
    print(f"[{'PASS' if passed else 'FAIL'}] {name}{('：' + detail) if detail else ''}")
    if not passed:
        failures += 1


def main() -> int:
    with TestClient(app) as client:
        token = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()[
            "access_token"
        ]
        admin = {"Authorization": f"Bearer {token}"}

        # 1. 应收台账：口径从「订单金额」改成「订单 − 核销」
        data = client.get("/api/receivables", headers=admin).json()
        if not data["items"]:
            check("应收台账", False, "没有应收数据，先造一张已确认订单再跑")
            return 1
        target = data["items"][0]
        customer_id = target["customer_id"]
        baseline = data["total"]
        check("应收台账", baseline > 0, f"{len(data['items'])} 个客户，合计 {baseline:,.2f}")

        # 2. 未结清订单
        orders = client.get(f"/api/receivables/{customer_id}/orders", headers=admin).json()
        check("未结清订单", bool(orders), f"{len(orders)} 张")
        if not orders:
            return 1
        first = orders[0]

        # 3. 登记收款：按先进先出自动核销
        amount = min(first["balance"], 100.0)
        payment = client.post(
            "/api/payments",
            headers=admin,
            json={"customer_id": customer_id, "amount": amount, "method": "银行转账", "reference": "TEST0001"},
        ).json()
        check(
            "收款自动核销",
            payment.get("allocations") and payment["allocations"][0]["amount"] == amount,
            f"{payment.get('payment_no')} → 核销 {len(payment.get('allocations', []))} 单",
        )

        after = client.get("/api/receivables", headers=admin).json()["total"]
        check("应收随核销下降", abs((baseline - amount) - after) < 0.01, f"{baseline:,.2f} → {after:,.2f}")

        # 4. 生成对账单快照
        statement = client.post(
            "/api/statements",
            headers=admin,
            json={"customer_id": customer_id, "period_start": "2020-01-01", "period_end": "2030-12-31"},
        ).json()
        check(
            "对账单快照",
            statement.get("content_hash") and statement.get("share_path"),
            f"{statement.get('statement_no')} 期末 {statement.get('closing_amount'):,.2f}",
        )
        share_path = statement["share_path"]
        code = statement["share_code"]

        # 5. 无口令看不到内容
        denied = client.post(share_path, data={"code": "000000"})
        check(
            "错误口令被拒",
            denied.status_code == 200 and "口令不正确" in denied.text and target["customer"] not in denied.text,
            "页面未泄露客户信息",
        )

        # 6. 正确口令可见
        opened = client.post(share_path, data={"code": code})
        check(
            "正确口令可见",
            opened.status_code == 200 and target["customer"] in opened.text and statement["content_hash"][:16] in opened.text,
        )

        # 7. 确认
        confirmed = client.post(f"{share_path}/action", data={"code": code, "action": "confirm", "note": ""})
        detail = client.get(f"/api/statements/{statement['id']}", headers=admin).json()
        check(
            "确认并留痕",
            detail["status"] == "confirmed" and any(e["event_type"] == "confirm" for e in detail["events"]),
            f"事件 {len(detail['events'])} 条",
        )

        # 8. 导出 Excel
        export = client.get(f"/api/statements/{statement['id']}/export", headers=admin)
        check("导出对账单", export.status_code == 200 and export.content[:2] == b"PK", f"{len(export.content)} 字节")

        # 9. 作废收款 → 应收回退
        client.post(f"/api/payments/{payment['id']}/void", headers=admin, params={"reason": "测试回退"})
        restored = client.get("/api/receivables", headers=admin).json()["total"]
        check("作废收款回退应收", abs(restored - baseline) < 0.01, f"{after:,.2f} → {restored:,.2f}")

    print()
    if failures:
        print(f"自检失败：{failures} 项不符合预期")
        return 1
    print("自检通过：收款核销、应收口径、对账单快照、公开确认与作废回退均符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
