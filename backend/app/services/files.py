"""上传文件落盘：按业务目录/年月分桶，返回可直接访问的 URL。"""
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from ..config import settings

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
ALLOWED_WORD_EXT = {".docx"}


def save_image_bytes(data: bytes, filename: str, folder: str = "inbound") -> tuple[str, str]:
    """保存图片字节，返回 (相对访问路径, 原始文件名)。"""
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXT:
        raise HTTPException(status_code=400, detail="仅支持 JPG / PNG / WEBP / BMP 图片")
    if not data:
        raise HTTPException(status_code=400, detail="图片内容为空")
    if len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"图片不能超过 {settings.MAX_UPLOAD_MB}MB")

    now = datetime.now()
    target_dir = settings.UPLOAD_DIR / folder / now.strftime("%Y%m")
    target_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid4().hex}{ext}"
    (target_dir / name).write_bytes(data)
    return f"/uploads/{folder}/{now.strftime('%Y%m')}/{name}", filename or name


def save_docx_bytes(data: bytes, filename: str, folder: str = "word_templates") -> tuple[str, str]:
    """保存客户上传的 Word 模板，返回 (相对访问路径, 原始文件名)。"""
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_WORD_EXT:
        raise HTTPException(status_code=400, detail="仅支持 .docx 格式的 Word 文件")
    if not data:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Word 文件不能超过 {settings.MAX_UPLOAD_MB}MB")

    now = datetime.now()
    target_dir = settings.UPLOAD_DIR / folder / now.strftime("%Y%m")
    target_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid4().hex}{ext}"
    (target_dir / name).write_bytes(data)
    return f"/uploads/{folder}/{now.strftime('%Y%m')}/{name}", filename or name


def upload_path(url: str) -> Path | None:
    """把 /uploads/... 访问路径还原成本地文件路径，越界路径直接拒绝。"""
    prefix = "/uploads/"
    if not url or not url.startswith(prefix):
        return None
    path = (settings.UPLOAD_DIR / url[len(prefix):]).resolve()
    if not path.is_relative_to(settings.UPLOAD_DIR.resolve()):
        return None
    return path
