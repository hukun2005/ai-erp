from io import BytesIO
from urllib.parse import quote

from docx import Document as WordDocument
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import require_perm
from ..models import DocumentTemplate, User
from ..schemas import TemplateOut, TemplateUpsert
from ..services.docx_render import build_sample_document
from ..services.files import save_docx_bytes, upload_path

router = APIRouter(prefix="/api/templates", tags=["单据模板"])

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _docx_response(data: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        BytesIO(data),
        media_type=DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def _serialize(tpl: DocumentTemplate) -> dict:
    return TemplateOut.model_validate(tpl).model_dump()


@router.get("", summary="单据模板列表")
def list_templates(
    doc_type: str = Query(""),
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("template:view")),
):
    stmt = select(DocumentTemplate).order_by(
        DocumentTemplate.doc_type, DocumentTemplate.is_default.desc(), DocumentTemplate.id
    )
    if doc_type:
        stmt = stmt.where(DocumentTemplate.doc_type == doc_type)
    return [_serialize(row) for row in db.scalars(stmt).all()]


@router.get("/default", summary="取某类单据的默认模板")
def default_template(
    doc_type: str = Query(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("template:view")),
):
    tpl = db.scalar(
        select(DocumentTemplate)
        .where(DocumentTemplate.doc_type == doc_type)
        .where(DocumentTemplate.enabled == True)  # noqa: E712
        .order_by(DocumentTemplate.is_default.desc(), DocumentTemplate.id)
    )
    return _serialize(tpl) if tpl else None


@router.post("", summary="新建单据模板")
def create_template(
    payload: TemplateUpsert,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("template:manage")),
):
    if payload.is_default:
        _clear_default(db, payload.doc_type)
    tpl = DocumentTemplate(
        doc_type=payload.doc_type,
        name=payload.name.strip(),
        title=payload.title.strip() or payload.name.strip(),
        is_default=payload.is_default,
        enabled=payload.enabled,
        config=payload.config,
        created_by=user.username,
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    record(
        username=user.username,
        role=user.role,
        action="create",
        resource="document_template",
        detail=f"新建单据模板 {tpl.name}（{tpl.doc_type}）",
        request=request,
    )
    return _serialize(tpl)


@router.put("/{template_id}", summary="修改单据模板")
def update_template(
    template_id: int,
    payload: TemplateUpsert,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("template:manage")),
):
    tpl = db.get(DocumentTemplate, template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    if payload.is_default:
        _clear_default(db, payload.doc_type, exclude_id=tpl.id)
    tpl.doc_type = payload.doc_type
    tpl.name = payload.name.strip()
    tpl.title = payload.title.strip() or payload.name.strip()
    tpl.is_default = payload.is_default
    tpl.enabled = payload.enabled
    tpl.config = payload.config
    db.commit()
    db.refresh(tpl)
    record(
        username=user.username,
        role=user.role,
        action="update",
        resource="document_template",
        detail=f"修改单据模板 {tpl.name}（{tpl.doc_type}）",
        request=request,
    )
    return _serialize(tpl)


@router.delete("/{template_id}", summary="删除单据模板")
def delete_template(
    template_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("template:manage")),
):
    tpl = db.get(DocumentTemplate, template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    db.delete(tpl)
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="delete",
        resource="document_template",
        detail=f"删除单据模板 {tpl.name}（{tpl.doc_type}）",
        request=request,
    )
    return {"ok": True}


WORD_SAMPLE_TITLES = {
    "sales_order": "销售订单",
    "purchase_order": "采购订单",
    "inbound": "入库单",
}


@router.get("/word-sample", summary="下载 Word 初始格式示例（含占位符）")
def word_sample(
    doc_type: str = Query("sales_order"),
    _: User = Depends(require_perm("template:view")),
):
    title = WORD_SAMPLE_TITLES.get(doc_type)
    if not title:
        raise HTTPException(status_code=400, detail="该单据类型暂不支持 Word 初始格式示例")
    return _docx_response(build_sample_document(doc_type), f"{title}_Word初始格式示例.docx")


@router.post("/{template_id}/word", summary="上传 Word 初始格式（.docx）")
async def upload_word_template(
    template_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("template:manage")),
):
    tpl = db.get(DocumentTemplate, template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在")

    data = await file.read()
    try:
        WordDocument(BytesIO(data))
    except Exception:
        raise HTTPException(status_code=400, detail="无法解析该 Word 文件，请确认是 .docx 格式")

    url, name = save_docx_bytes(data, file.filename or "")
    # 替换旧文件，避免上传目录里堆积无引用的模板
    old = upload_path(tpl.word_file) if tpl.word_file else None
    if old and old.is_file():
        old.unlink(missing_ok=True)
    tpl.word_file = url
    tpl.word_filename = name
    db.commit()
    db.refresh(tpl)
    record(
        username=user.username,
        role=user.role,
        action="upload",
        resource="document_template",
        detail=f"上传 Word 模板 {name}（{tpl.name}）",
        request=request,
    )
    return _serialize(tpl)


@router.delete("/{template_id}/word", summary="移除 Word 初始格式")
def remove_word_template(
    template_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("template:manage")),
):
    tpl = db.get(DocumentTemplate, template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    old = upload_path(tpl.word_file) if tpl.word_file else None
    if old and old.is_file():
        old.unlink(missing_ok=True)
    filename = tpl.word_filename
    tpl.word_file = ""
    tpl.word_filename = ""
    db.commit()
    db.refresh(tpl)
    record(
        username=user.username,
        role=user.role,
        action="delete",
        resource="document_template",
        detail=f"移除 Word 模板 {filename}（{tpl.name}）",
        request=request,
    )
    return _serialize(tpl)


def _clear_default(db: Session, doc_type: str, exclude_id: int | None = None) -> None:
    rows = db.scalars(
        select(DocumentTemplate)
        .where(DocumentTemplate.doc_type == doc_type)
        .where(DocumentTemplate.is_default == True)  # noqa: E712
    ).all()
    for row in rows:
        if exclude_id and row.id == exclude_id:
            continue
        row.is_default = False
