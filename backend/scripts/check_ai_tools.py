"""AI 工具层与问答链路自检。

不启动服务、不改动业务库：脚本把 erp.db 复制到临时目录后连接副本，
因此审计日志和查询都不会写进真实数据。

用法（在 backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\check_ai_tools.py
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

# 必须在导入 app.config 之前设置：config 在类定义时读取该变量
_source_db = BASE_DIR / "erp.db"
_temp_db = Path(tempfile.gettempdir()) / "erp_check_ai_tools.db"
if _source_db.exists():
    shutil.copy(_source_db, _temp_db)
os.environ["DATABASE_URL"] = f"sqlite:///{_temp_db.as_posix()}"

from sqlalchemy import select  # noqa: E402

from app.ai.tools import TOOLS, available_tools, execute  # noqa: E402
from app.ai.tools.base import ToolContext  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Customer, User  # noqa: E402
from app.scoping import scope_owner  # noqa: E402
from app.services import ai_service  # noqa: E402

# (角色, 工具名, 参数, 期望的错误码；None 表示期望成功)
CASES = [
    ("admin", "query_sales", {"metric": "revenue", "group_by": "none", "days": 30}, None),
    ("admin", "query_sales", {"metric": "qty", "group_by": "category", "days": 30}, None),
    ("admin", "query_sales", {"metric": "revenue", "group_by": "day", "days": 7}, None),
    ("admin", "search_orders", {"limit": 3}, None),
    ("admin", "get_stock", {"limit": 3}, None),
    ("admin", "get_price_history", {"limit": 3}, None),
    ("admin", "get_receivable", {"limit": 3}, None),
    ("admin", "search_products", {"limit": 3}, None),
    ("admin", "search_purchase_orders", {"limit": 3}, None),
    ("purchaser", "get_replenishment_plan", {"limit": 3}, None),
    # 越权：销售员无 finance:view，查应收必须被拒
    ("sales", "get_receivable", {}, "forbidden"),
    # 越权：销售员无 ai:replenish，补货测算必须被拒
    ("sales", "get_replenishment_plan", {}, "forbidden"),
    # 越权：销售员无 purchase:view，采购单检索必须被拒
    ("sales", "search_purchase_orders", {}, "forbidden"),
    # 销售员无 inventory:view，但可查库存数量（成本字段单独断言）
    ("sales", "get_stock", {"limit": 3}, None),
    # 同理：销售员可查商品与客户，但字段裁剪另行断言
    ("sales", "search_products", {"limit": 3}, None),
    # 非法组合：营收不支持按商品分组
    ("admin", "query_sales", {"metric": "revenue", "group_by": "product"}, "invalid_args"),
    # 非法参数：days 越界
    ("admin", "query_sales", {"metric": "revenue", "group_by": "none", "days": 99999}, "invalid_args"),
    # 不存在的工具
    ("admin", "drop_database", {}, "unknown_tool"),
]


def make_user(role: str) -> User:
    """构造未入库的测试用户，避免污染 users 表。"""
    return User(id=0, username="checker", role=role, full_name="自检", hashed_password="", is_active=True)


def make_context(role: str) -> ToolContext:
    user = make_user(role)
    return ToolContext(db=SessionLocal(), user=user, owner=scope_owner(user))


def row_count(data) -> int:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("rows", "items", "recent_orders"):
            if isinstance(data.get(key), list):
                return len(data[key])
    return 1


def main() -> int:
    print(f"已注册工具 {len(TOOLS)} 个：{'、'.join(TOOLS)}")
    print(f"admin 可用 {len(available_tools('admin'))} 个，sales 可用 {len(available_tools('sales'))} 个\n")

    failures = 0

    db = SessionLocal()
    try:
        customer_name = db.scalar(select(Customer.name).order_by(Customer.id).limit(1))
    finally:
        db.close()
    if customer_name:
        CASES.append(("admin", "get_customer_profile", {"keyword": customer_name}, None))
        CASES.append(("admin", "search_customers", {"keyword": customer_name}, None))

    for role, name, args, expected_error in CASES:
        ctx = make_context(role)
        try:
            result = execute(name, args, ctx)
        finally:
            ctx.db.close()
        if expected_error is None:
            passed = result.ok
            detail = f"返回 {row_count(result.data)} 条"
        else:
            passed = (not result.ok) and result.error == expected_error
            detail = f"{result.error}：{result.error_detail}"
        print(f"[{'PASS' if passed else 'FAIL'}] {role:<6} {name:<20} {detail}")
        failures += 0 if passed else 1

    # 字段级裁剪：sales 查库存不应看到成本价
    ctx = make_context("sales")
    try:
        stock = execute("get_stock", {"limit": 3}, ctx).data or {}
    finally:
        ctx.db.close()
    if any("cost_price" in row for row in stock.get("rows", [])):
        print("[FAIL] 字段裁剪：sales 角色看到了 cost_price")
        failures += 1
    else:
        print("[PASS] 字段裁剪：sales 角色看不到 cost_price")

    # 商品检索同样按字段权限裁剪成本价
    ctx = make_context("sales")
    try:
        products = execute("search_products", {"limit": 3}, ctx).data or {}
    finally:
        ctx.db.close()
    if any("cost_price" in row for row in products.get("rows", [])):
        print("[FAIL] 字段裁剪：sales 通过 search_products 看到了 cost_price")
        failures += 1
    else:
        print("[PASS] 字段裁剪：search_products 对 sales 隐藏 cost_price")

    # 问答链路：无密钥时应降级到规则引擎，且不抛异常
    db = SessionLocal()
    try:
        admin = make_user("admin")
        reply = ai_service.chat(db, "哪些商品需要补货？", [], admin)
        source = reply.get("source")
        answer = reply.get("answer") or ""
        passed = bool(answer) and source in ("rule", "llm")
        print(f"[{'PASS' if passed else 'FAIL'}] 问答降级：source={source}，回答 {len(answer)} 字")
        failures += 0 if passed else 1

        # 补货建议必须带在途字段，否则无法判断是否重复下单
        suggestions = ai_service.get_replenishment(db)
        passed = all("in_transit" in item for item in suggestions)
        print(f"[{'PASS' if passed else 'FAIL'}] 补货建议含在途：{len(suggestions)} 条")
        failures += 0 if passed else 1
    finally:
        db.close()

    print()
    if failures:
        print(f"自检失败：{failures} 项不符合预期")
        return 1
    print("自检通过：权限、参数校验、数据范围、问答降级与补货口径均符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
