"""单据打印：服务端 PDF、批量合并、水印/多联、套打校准与打印留痕。"""
from datetime import datetime
from io import BytesIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import get_current_user
from ..models import DocumentTemplate, PrintJob, User
from ..permissions import has_permission
from ..schemas import PrintBatchIn
from ..services import pdf_render, print_data

router = APIRouter(prefix="/api/print", tags=["单据打印"])

DOC_PERMISSIONS = {
    "sales_order": "sales:view",
    "purchase_order": "purchase:view",
    "inbound": "inbound:view",
    "outbound": "outbound:view",
}
# 单据类型 → 单据模板的 doc_type（出库单单独一类，没有模板时用内置版式）
TEMPLATE_DOC_TYPE = {
    "sales_order": "sales_order",
    "purchase_order": "purchase_order",
    "inbound": "inbound",
    "outbound": "sales_outbound",
}
MAX_BATCH = 50


def _ensure_perm(user: User, doc_type: str) -> None:
    if doc_type not in DOC_PERMISSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的打印单据类型：{doc_type}")
    permission = DOC_PERMISSIONS[doc_type]
    if not has_permission(user.role, permission):
        raise HTTPException(status_code=403, detail=f"当前角色无此操作权限（{permission}）")


def _load_template(db: Session, doc_type: str, template_id: int | None) -> dict | None:
    """优先用指定模板，其次用该单据类型的默认模板。"""
    if template_id:
        row = db.get(DocumentTemplate, template_id)
        if not row:
            raise HTTPException(status_code=404, detail="单据模板不存在")
        return {"title": row.title, "config": row.config or {}}
    template_doc_type = TEMPLATE_DOC_TYPE.get(doc_type, doc_type)
    row = db.scalar(
        select(DocumentTemplate)
        .where(DocumentTemplate.doc_type == template_doc_type)
        .where(DocumentTemplate.enabled == True)  # noqa: E712
        .order_by(DocumentTemplate.is_default.desc(), DocumentTemplate.id)
    )
    if row is None:
        return None
    return {"title": row.title, "config": row.config or {}}


def _options(
    *,
    copies: int = 1,
    watermark: str = "",
    paper: str = "A4",
    width_mm: float | None = None,
    height_mm: float | None = None,
    offset_x_mm: float = 0.0,
    offset_y_mm: float = 0.0,
    copy_labels: list[str] | None = None,
    barcode: bool = True,
) -> pdf_render.PrintOptions:
    return pdf_render.PrintOptions(
        paper=paper or "A4",
        width_mm=width_mm,
        height_mm=height_mm,
        copies=max(1, min(int(copies or 1), 8)),
        copy_labels=[label for label in (copy_labels or []) if label],
        watermark=(watermark or "").strip()[:20],
        offset_x_mm=float(offset_x_mm or 0),
        offset_y_mm=float(offset_y_mm or 0),
        barcode=bool(barcode),
    )


def _pdf_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "no-store",
        },
    )


def _record_job(db: Session, job: PrintJob) -> None:
    db.add(job)
    db.commit()


@router.get("/{doc_type}/{doc_id}/pdf", summary="生成单张单据 PDF")
def print_pdf(
    doc_type: str,
    doc_id: int,
    template_id: int | None = Query(None),
    copies: int = Query(1, ge=1, le=8),
    watermark: str = Query(""),
    paper: str = Query("A4"),
    width_mm: float | None = Query(None, gt=0),
    height_mm: float | None = Query(None, gt=0),
    offset_x_mm: float = Query(0),
    offset_y_mm: float = Query(0),
    labels: str = Query("", description="多联标签，逗号分隔，如：存根,客户,财务"),
    barcode: bool = Query(True),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _ensure_perm(user, doc_type)
    spec = print_data.build_spec(db, doc_type, doc_id)
    if not spec:
        raise HTTPException(status_code=404, detail="单据不存在")

    options = _options(
        copies=copies,
        watermark=watermark,
        paper=paper,
        width_mm=width_mm,
        height_mm=height_mm,
        offset_x_mm=offset_x_mm,
        offset_y_mm=offset_y_mm,
        copy_labels=[item.strip() for item in labels.split(",") if item.strip()],
        barcode=barcode,
    )
    template = _load_template(db, doc_type, template_id)
    content = pdf_render.render_document(spec, options, template)
    pages = len(content) and _page_count(content)
    _record_job(
        db,
        PrintJob(
            kind="single",
            doc_type=doc_type,
            doc_ids=str(doc_id),
            doc_nos=spec.get("doc_no", ""),
            template_id=template_id,
            copies=options.copies,
            watermark=options.watermark,
            page_count=pages,
            size_bytes=len(content),
            operator=user.username,
            created_at=datetime.now(),
        ),
    )
    return _pdf_response(content, f"{spec.get('doc_no') or spec.get('title', '单据')}.pdf")


def _page_count(content: bytes) -> int:
    from pypdf import PdfReader

    return len(PdfReader(BytesIO(content)).pages)


@router.post("/batch", summary="批量打印（合并成一个 PDF）")
def print_batch(
    payload: PrintBatchIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _ensure_perm(user, payload.doc_type)
    ids = [int(item) for item in payload.ids if int(item) > 0]
    if not ids:
        raise HTTPException(status_code=400, detail="请先选择要打印的单据")
    if len(ids) > MAX_BATCH:
        raise HTTPException(status_code=400, detail=f"单次最多合并 {MAX_BATCH} 张单据")

    options = _options(
        copies=payload.copies,
        watermark=payload.watermark,
        paper=payload.paper,
        width_mm=payload.width_mm,
        height_mm=payload.height_mm,
        offset_x_mm=payload.offset_x_mm,
        offset_y_mm=payload.offset_y_mm,
        copy_labels=payload.copy_labels,
        barcode=payload.barcode,
    )
    template = _load_template(db, payload.doc_type, payload.template_id)

    documents = []
    doc_nos = []
    for doc_id in ids:
        spec = print_data.build_spec(db, payload.doc_type, doc_id)
        if not spec:
            continue
        documents.append(pdf_render.render_document(spec, options, template))
        doc_nos.append(str(spec.get("doc_no", "")))
    if not documents:
        raise HTTPException(status_code=404, detail="所选单据都不存在或已删除")

    merged = pdf_render.merge_pdfs(documents)
    pages = _page_count(merged)
    _record_job(
        db,
        PrintJob(
            kind="batch",
            doc_type=payload.doc_type,
            doc_ids=",".join(str(item) for item in ids)[:300],
            doc_nos=",".join(doc_nos)[:600],
            template_id=payload.template_id,
            copies=options.copies,
            watermark=options.watermark,
            page_count=pages,
            size_bytes=len(merged),
            operator=user.username,
            created_at=datetime.now(),
        ),
    )
    record(
        username=user.username,
        role=user.role,
        action="批量打印",
        resource=f"{print_data.DOC_TYPES.get(payload.doc_type, payload.doc_type)} × {len(documents)}",
        detail=f"合并 {pages} 页 PDF",
        request=request,
    )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return _pdf_response(merged, f"批量打印_{stamp}.pdf")


@router.get("/calibration/sheet", summary="套打校准页")
def calibration(
    offset_x_mm: float = Query(0),
    offset_y_mm: float = Query(0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    content = pdf_render.calibration_sheet(float(offset_x_mm or 0), float(offset_y_mm or 0))
    _record_job(
        db,
        PrintJob(
            kind="calibration",
            doc_type="",
            doc_ids="",
            doc_nos="",
            copies=1,
            page_count=_page_count(content),
            size_bytes=len(content),
            operator=user.username,
            created_at=datetime.now(),
        ),
    )
    return _pdf_response(content, "套打校准页.pdf")


@router.get("/jobs", summary="打印记录")
def list_jobs(
    doc_type: str = Query(""),
    limit: int = Query(30, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(PrintJob).order_by(PrintJob.id.desc()).limit(limit)
    if doc_type:
        stmt = stmt.where(PrintJob.doc_type == doc_type)
    rows = db.scalars(stmt).all()
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "doc_type": row.doc_type,
            "doc_type_text": print_data.DOC_TYPES.get(row.doc_type, row.doc_type),
            "doc_nos": row.doc_nos,
            "copies": row.copies,
            "watermark": row.watermark,
            "page_count": row.page_count,
            "size_bytes": row.size_bytes,
            "operator": row.operator,
            "created_at": row.created_at,
        }
        for row in rows
    ]
