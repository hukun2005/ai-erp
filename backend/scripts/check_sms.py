"""短信验证码模式自检：走完整发送 → 校验 → 确认链路。

用 console 提供方 + 拦截 send_code 拿到验证码，不产生任何外部调用。

用法（在 backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\check_sms.py
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

_temp_db = Path(tempfile.gettempdir()) / "erp_check_sms.db"
for suffix in ("", "-wal", "-shm"):
    stale = Path(str(_temp_db) + suffix)
    if stale.exists():
        stale.unlink()
shutil.copy(BASE_DIR / "erp.db", _temp_db)
os.environ["DATABASE_URL"] = f"sqlite:///{_temp_db.as_posix()}"
os.environ["SMS_PROVIDER"] = "console"  # 必须在导入 config 之前设置

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import sms  # noqa: E402

sent: dict[str, str] = {}


def fake_send(phone: str, code: str):
    """拦截真实发送，记录验证码以便断言。"""
    sent["phone"] = phone
    sent["code"] = code
    return True, "console"


sms.send_code = fake_send

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

        items = client.get("/api/receivables", headers=admin).json()["items"]
        if not items:
            check("准备数据", False, "没有应收数据")
            return 1
        customer_id = items[0]["customer_id"]
        # 确保客户有手机号，否则短信无法下发
        client.put(f"/api/customers/{customer_id}", headers=admin, json={"phone": "13800138000"})

        statement = client.post(
            "/api/statements",
            headers=admin,
            json={"customer_id": customer_id, "period_start": "2020-01-01", "period_end": "2030-12-31"},
        ).json()
        path = statement["share_path"]

        # 1. 未发送验证码前，任意码都应被拒
        denied = client.post(path, data={"code": "000000"})
        check("无验证码时被拒", "验证码不正确" in denied.text)

        # 2. 发送验证码
        sent.clear()
        sent_page = client.post(f"{path}/send-code")
        code = sent.get("code", "")
        check(
            "发送验证码",
            sent.get("phone") == "13800138000" and len(code) == 6 and "已发送" in sent_page.text,
            f"发往 {sent.get('phone')}",
        )

        # 3. 错误验证码被拒
        check("错误验证码被拒", "验证码不正确" in client.post(path, data={"code": "111111"}).text)

        # 4. 正确验证码可查看
        opened = client.post(path, data={"code": code})
        check(
            "正确验证码可查看",
            items[0]["customer"] in opened.text and statement["content_hash"][:16] in opened.text,
        )

        # 5. 查看不消费验证码，可继续确认
        confirmed = client.post(f"{path}/action", data={"code": code, "action": "confirm", "note": ""})
        detail = client.get(f"/api/statements/{statement['id']}", headers=admin).json()
        check(
            "确认成功并留痕",
            detail["status"] == "confirmed"
            and any(e["event_type"] == "confirm" and e["code_verified"] for e in detail["events"]),
            f"事件 {len(detail['events'])} 条",
        )

        # 6. 验证码已被消费，不能重复使用
        reuse = client.post(f"{path}/action", data={"code": code, "action": "dispute", "note": "x"})
        check("验证码一次性", "验证码不正确" in reuse.text or "已确认" in reuse.text)

    print()
    if failures:
        print(f"自检失败：{failures} 项不符合预期")
        return 1
    print("自检通过：短信验证码的发送、校验、消费与留痕均符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
