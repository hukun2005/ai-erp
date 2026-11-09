"""图片识别接口：把单据照片转成草稿，供入库/采购/销售各单据页复用。

识别结果不直接落库：接口只返回结构化草稿与商品候选，由用户核对后再保存。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from ..audit import record
from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..permissions import has_permission
from ..services import vision
from ..services.files import save_image_bytes

router = APIRouter(prefix="/api/vision", tags=["图片识别"])


@router.get("/status", summary="图片识别可用状态与配置")
def recognize_status(user: User = Depends(get_current_user)):
    """前端据此决定是否展示「拍照识别」入口，以及提示用哪个模型。"""
    return {
        "enabled": settings.vision_enabled,
        "model": settings.VISION_MODEL,
        "max_pages": settings.VISION_MAX_PAGES,
        "doc_types": [
            {
                "key": spec.key,
                "label": spec.label,
                "permission": spec.permission,
                "allowed": has_permission(user.role, spec.permission),
            }
            for spec in vision.DOC_SPECS.values()
        ],
    }


async def recognize_uploaded(
    db: Session,
    files: list[UploadFile],
    doc_type: str,
    request: Request | None = None,
    user: User | None = None,
) -> dict:
    """读取上传的照片、落盘、调用识别，并把照片路径合并进结果。"""
    spec = vision.DOC_SPECS.get(doc_type)
    if not spec:
        raise HTTPException(status_code=400, detail=f"不支持的单据类型：{doc_type}")
    if user is not None and not has_permission(user.role, spec.permission):
        raise HTTPException(status_code=403, detail=f"当前角色无此操作权限（{spec.permission}）")
    if len(files) > settings.VISION_MAX_PAGES:
        # 先挡掉超页请求，避免识别失败了还留下一堆无用照片
        raise HTTPException(
            status_code=400,
            detail=f"一次最多识别 {settings.VISION_MAX_PAGES} 页，请分次识别",
        )

    images: list[tuple[bytes, str]] = []
    pages: list[dict] = []
    for upload in files:
        data = await upload.read()
        path, name = save_image_bytes(data, upload.filename or "", folder=doc_type)
        images.append((data, name))
        pages.append({"path": path, "name": name})

    result = vision.recognize_images(db, images, doc_type)
    result["photo_path"] = pages[0]["path"]
    result["photo_name"] = pages[0]["name"]
    result["photo_paths"] = pages
    result["page_meta"] = result.get("pages") or []
    if request is not None and user is not None:
        record(
            username=user.username,
            role=user.role,
            action="recognize",
            resource=doc_type,
            detail=f"识别{spec.label} {pages[0]['name']}（{len(pages)} 页）",
            request=request,
        )
    return result


@router.post("/recognize", summary="上传单据照片并识别（入库/采购/销售）")
async def recognize(
    request: Request,
    files: list[UploadFile] = File(..., description="单据照片，可多页"),
    doc_type: str = Form("inbound", description="inbound / purchase / sales"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await recognize_uploaded(db, files, doc_type, request, user)
