"""交付物审计：一次性确认关键能力是否仍在。

用法（在 backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\audit.py
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_temp = Path(tempfile.gettempdir()) / "erp_audit.db"
for suffix in ("", "-wal", "-shm"):
    stale = Path(str(_temp) + suffix)
    if stale.exists():
        stale.unlink()
shutil.copy(BASE_DIR / "erp.db", _temp)
os.environ["DATABASE_URL"] = f"sqlite:///{_temp.as_posix()}"

from app.ai.tools import TOOLS  # noqa: E402
from app.main import app  # noqa: E402

print(f"已注册 AI 工具 {len(TOOLS)} 个：{'、'.join(TOOLS)}\n")

groups = {}
for route in app.routes:
    path = getattr(route, "path", "")
    if not path.startswith("/api"):
        continue
    parts = path.split("/")
    key = f"/{parts[1]}/{parts[2]}" if len(parts) > 2 else path
    groups.setdefault(key, 0)
    groups[key] += 1
print(f"接口分组（共 {len(app.routes)} 条路由）：")
for key in sorted(groups):
    print(f"  {key:<32} {groups[key]} 个")
print()

connection = sqlite3.connect(_temp)
tables = [row[0] for row in connection.execute(
    "select name from sqlite_master where type='table' order by name"
)]
print(f"数据库表 {len(tables)} 张：")
for name in tables:
    count = connection.execute(f'select count(*) from "{name}"').fetchone()[0]
    print(f"  {name:<28} {count} 行")
connection.close()
