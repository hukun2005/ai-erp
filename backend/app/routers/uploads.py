from fastapi import APIRouter, Depends, File, Request, UploadFile

from ..audit import record
from ..deps import require_any_perm
from ..models import User
from ..services.files import save_image_bytes

router = APIRouter(prefix="/api/uploads", tags=["文件上传"])


@router.post("/image", summary="上传图片（模板 Logo 等）")
async def upload_image(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_any_perm("template:manage", "inbound:manage")),
):
    data = await file.read()
    url, name = save_image_bytes(data, file.filename or "", folder="image")
    record(
        username=user.username,
        role=user.role,
        action="upload",
        resource="file",
        detail=f"上传图片 {name}",
        request=request,
    )
    return {"url": url, "name": name}
