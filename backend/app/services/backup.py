"""数据备份与恢复。

备份用 sqlite3 的 online backup API 做一致性快照（无需停服），
连同 uploads 目录一起打成 zip，便于整包归档、下载与异地保存；
恢复则反向把备份库写回运行库并整体替换 uploads，之后 dispose 连接池。
"""
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from ..config import DATA_DIR, settings
from ..database import engine


def backup_dir() -> Path:
    path = DATA_DIR / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _db_path() -> Path:
    url = settings.DATABASE_URL
    if url.startswith("sqlite:///"):
        raw = url[len("sqlite:///"):]
        if raw:
            return Path(raw)
    return DATA_DIR / "erp.db"


def last_backup_at() -> datetime | None:
    files = sorted(backup_dir().glob("*.zip"), key=lambda p: p.stat().st_mtime)
    if not files:
        return None
    return datetime.fromtimestamp(files[-1].stat().st_mtime)


def list_backups() -> list[dict]:
    rows = []
    for path in sorted(backup_dir().glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = path.stat()
        rows.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return rows


def _snapshot_db(target: Path) -> None:
    """把运行库一致性快照写入 target（sqlite online backup）。"""
    db_src = _db_path()
    if not db_src.is_file():
        return
    dest = sqlite3.connect(str(target))
    try:
        with dest:
            src = sqlite3.connect(str(db_src))
            try:
                src.backup(dest)
            finally:
                src.close()
    finally:
        dest.close()


def create_backup() -> dict:
    now = datetime.now()
    filename = f"backup_{now.strftime('%Y%m%d_%H%M%S')}.zip"
    target = backup_dir() / filename

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        db_copy = tmp_dir / "erp.db"
        _snapshot_db(db_copy)

        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            if db_copy.is_file():
                zf.write(db_copy, "erp.db")
            if settings.UPLOAD_DIR.is_dir():
                for file in settings.UPLOAD_DIR.rglob("*"):
                    if file.is_file():
                        zf.write(file, f"uploads/{file.relative_to(settings.UPLOAD_DIR)}")

    return {
        "name": filename,
        "size": target.stat().st_size,
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _safe_name(name: str) -> Path:
    path = (backup_dir() / name).resolve()
    if not name.endswith(".zip") or path.parent != backup_dir().resolve() or not path.is_file():
        raise ValueError("备份文件不存在")
    return path


def restore_backup(name: str) -> str:
    path = _safe_name(name)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        with zipfile.ZipFile(path) as zf:
            zf.extractall(tmp_dir)

        db_file = tmp_dir / "erp.db"
        if not db_file.is_file():
            raise ValueError("备份中缺少数据库文件")

        # 恢复数据库：在线一致性写回，无需停服
        db_target = _db_path()
        db_target.parent.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(str(db_file))
        try:
            dest = sqlite3.connect(str(db_target))
            try:
                src.backup(dest)
            finally:
                dest.close()
        finally:
            src.close()
        engine.dispose()

        # 恢复上传目录
        uploads = tmp_dir / "uploads"
        if uploads.is_dir():
            if settings.UPLOAD_DIR.is_dir():
                shutil.rmtree(settings.UPLOAD_DIR)
            shutil.copytree(uploads, settings.UPLOAD_DIR)

    return name


def delete_backup(name: str) -> None:
    path = _safe_name(name)
    path.unlink()