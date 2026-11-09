"""接口级自检：用 TestClient 跑一遍新增的编辑、导入与开单预览链路。

不启动服务、不改动业务库：脚本把 erp.db 复制到临时目录后连接副本。

用法（在 backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\check_api.py
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
_temp_db = Path(tempfile.gettempdir()) / "erp_check_api.db"
if _source_db.exists():
    shutil.copy(_source_db, _temp_db)
for suffix in ("-wal", "-shm"):
    stale = Path(str(_temp_db) + suffix)
    if stale.exists():
        stale.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_temp_db.as_posix()}"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
failures = 0


def check(name: str, passed: bool, detail: str = "") -> None:
    global failures
    print(f"[{'PASS' if passed else 'FAIL'}] {name}{('：' + detail) if detail else ''}")
    if not passed:
        failures += 1


def login(client: TestClient, username: str, password: str) -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def main() -> int:
    with TestClient(app) as client:
        admin = login(client, "admin", "admin123")
        sales = login(client, "sales01", "123456")

        # 1. 工具清单按角色过滤
        tools = client.get("/api/ai/tools", headers=admin).json()["items"]
        names = {item["name"] for item in tools}
        check("工具清单含开单预览", "propose_sales_order" in names, f"{len(names)} 个工具")
        sales_tools = {item["name"] for item in client.get("/api/ai/tools", headers=sales).json()["items"]}
        check("销售角色无应收工具", "get_receivable" not in sales_tools)

        # 2. 越权被拒且不返回数据
        denied = client.post(
            "/api/ai/tools/run",
            headers=sales,
            json={"name": "get_receivable", "arguments": {}},
        ).json()
        check("越权调用被拒", denied["ok"] is False and denied["error"] == "forbidden")

        # 3. 开单预览：产出草稿但不落库
        customers = client.get("/api/customers/all", headers=admin).json()
        products = client.get("/api/products", headers=admin, params={"page_size": 1}).json()["items"]
        if not customers or not products:
            check("开单预览", False, "种子数据缺少客户或商品")
        else:
            before = client.get("/api/sales-orders", headers=admin, params={"page_size": 1}).json()["total"]
            preview = client.post(
                "/api/ai/tools/run",
                headers=admin,
                json={
                    "name": "propose_sales_order",
                    "arguments": {
                        "customer": customers[0]["name"],
                        "items": [{"product": products[0]["sku"], "quantity": 2}],
                    },
                },
            ).json()
            after = client.get("/api/sales-orders", headers=admin, params={"page_size": 1}).json()["total"]
            payload = preview.get("data") or {}
            check(
                "开单预览返回草稿",
                preview["ok"] and payload.get("found") and payload.get("draft", {}).get("items"),
                f"{payload.get('total_amount')} 元",
            )
            check("开单预览不写库", before == after, f"{before} → {after}")

        # 4. 商品导入模板可下载且能被重新导入
        template = client.get("/api/products/import-template", headers=admin)
        check(
            "导入模板下载",
            template.status_code == 200 and template.content[:2] == b"PK",
            f"{len(template.content)} 字节",
        )
        imported = client.post(
            "/api/products/import",
            headers=admin,
            files={"file": ("products.xlsx", template.content, XLSX)},
        ).json()
        check("商品导入", imported.get("created", 0) >= 1, f"新增 {imported.get('created')} 条")
        again = client.post(
            "/api/products/import",
            headers=admin,
            files={"file": ("products.xlsx", template.content, XLSX)},
        ).json()
        check("重复导入跳过", again.get("created") == 0 and again.get("skipped") >= 1)

        # 5. 客户导入模板
        customer_template = client.get("/api/customers/import-template", headers=admin)
        check(
            "客户模板下载",
            customer_template.status_code == 200 and customer_template.content[:2] == b"PK",
        )

        # 6. 草稿订单编辑：改数量后金额重算
        if customers and products:
            created = client.post(
                "/api/sales-orders",
                headers=admin,
                json={
                    "customer_id": customers[0]["id"],
                    "discount_amount": 0,
                    "items": [{"product_id": products[0]["id"], "quantity": 2, "unit_price": 100}],
                },
            ).json()
            updated = client.put(
                f"/api/sales-orders/{created['id']}",
                headers=admin,
                json={
                    "discount_amount": 10,
                    "items": [{"product_id": products[0]["id"], "quantity": 5, "unit_price": 100}],
                },
            ).json()
            check(
                "草稿订单编辑重算金额",
                updated["total_amount"] == 490 and updated["items"][0]["quantity"] == 5,
                f"{created['total_amount']} → {updated['total_amount']}",
            )
            client.delete(f"/api/sales-orders/{created['id']}", headers=admin)

            # 已确认订单不允许再编辑
            second = client.post(
                "/api/sales-orders",
                headers=admin,
                json={
                    "customer_id": customers[0]["id"],
                    "items": [{"product_id": products[0]["id"], "quantity": 1, "unit_price": 100}],
                },
            ).json()
            confirm = client.post(
                f"/api/sales-orders/{second['id']}/status", headers=admin, json={"action": "confirm"}
            )
            blocked = client.put(f"/api/sales-orders/{second['id']}", headers=admin, json={"remark": "x"})
            check(
                "非草稿禁止编辑",
                confirm.status_code == 200 and blocked.status_code == 400,
                f"确认 {confirm.status_code}，编辑 {blocked.status_code}",
            )

    print()
    if failures:
        print(f"自检失败：{failures} 项不符合预期")
        return 1
    print("自检通过：工具鉴权、开单预览、批量导入与草稿编辑均符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
