import io
from datetime import date, datetime
from urllib.parse import quote

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db, require_perm
from ..models import Invoice, User
from ..schemas import InvoiceAction, InvoiceCreate, InvoiceOut, InvoiceUpdate
from ..services import importer
from ..services import matching
from ..services.docx_render import doc_to_bytes, docx_response, money

router = APIRouter(prefix="/api/invoices", tags=["发票管理"])


def _payload(inv: Invoice) -> dict:
    return InvoiceOut.model_validate(inv).model_dump()


@router.get("", dependencies=[Depends(require_perm("invoice:view"))])
def list_invoices(
    direction: str | None = None,
    keyword: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = select(Invoice)
    if direction:
        stmt = stmt.where(Invoice.direction == direction)
    if status:
        stmt = stmt.where(Invoice.status == status)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            or_(
                Invoice.invoice_no.like(like),
                Invoice.partner_name.like(like),
                Invoice.order_no.like(like),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Invoice.issue_date.desc(), Invoice.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"total": total, "items": [_payload(i) for i in rows]}


@router.get("/three-way", dependencies=[Depends(require_perm("invoice:view"))])
def three_way_match(db: Session = Depends(get_db)):
    return matching.three_way_match(db)


@router.get("/summary", dependencies=[Depends(require_perm("invoice:view"))])
def invoice_summary(db: Session = Depends(get_db)):
    rows = db.execute(
        select(
            Invoice.direction,
            func.count(Invoice.id),
            func.coalesce(func.sum(Invoice.total_amount), 0),
        ).group_by(Invoice.direction)
    ).all()
    by_dir = {d: {"count": int(c), "total": round(float(s), 2)} for d, c, s in rows}
    pending_verify = (
        db.scalar(
            select(func.count(Invoice.id)).where(
                Invoice.direction == "purchase",
                Invoice.certified.is_(False),
                Invoice.status == "issued",
            )
        )
        or 0
    )
    return {
        "sales": by_dir.get("sales", {"count": 0, "total": 0.0}),
        "purchase": by_dir.get("purchase", {"count": 0, "total": 0.0}),
        "pending_verify": pending_verify,
    }


@router.post("", dependencies=[Depends(require_perm("invoice:manage"))])
def create_invoice(
    payload: InvoiceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if db.scalar(select(Invoice.id).where(Invoice.invoice_no == payload.invoice_no.strip())):
        raise HTTPException(status_code=400, detail="发票号码已存在")
    data = payload.model_dump()
    data["invoice_no"] = data["invoice_no"].strip()
    if not data["invoice_no"]:
        raise HTTPException(status_code=400, detail="发票号码不能为空")
    data["issue_date"] = payload.issue_date or datetime.now()
    inv = Invoice(**data, created_by=user.username)
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return _payload(inv)


@router.put("/{invoice_id}", dependencies=[Depends(require_perm("invoice:manage"))])
def update_invoice(invoice_id: int, payload: InvoiceUpdate, db: Session = Depends(get_db)):
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="发票不存在")
    data = payload.model_dump(exclude_unset=True)
    if data.get("invoice_no"):
        data["invoice_no"] = data["invoice_no"].strip()
        if data["invoice_no"] != inv.invoice_no and db.scalar(
            select(Invoice.id).where(
                Invoice.invoice_no == data["invoice_no"], Invoice.id != invoice_id
            )
        ):
            raise HTTPException(status_code=400, detail="发票号码已存在")
    for key, value in data.items():
        setattr(inv, key, value)
    db.commit()
    db.refresh(inv)
    return _payload(inv)


@router.post("/{invoice_id}/status", dependencies=[Depends(require_perm("invoice:manage"))])
def change_status(invoice_id: int, payload: InvoiceAction, db: Session = Depends(get_db)):
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="发票不存在")
    if payload.action == "issue":
        inv.status = "issued"
    elif payload.action == "void":
        inv.status = "voided"
    elif payload.action == "certify":
        inv.certified = True
        inv.certified_at = datetime.now()
    db.commit()
    db.refresh(inv)
    return _payload(inv)


@router.delete("/{invoice_id}", dependencies=[Depends(require_perm("invoice:manage"))])
def delete_invoice(invoice_id: int, db: Session = Depends(get_db)):
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="发票不存在")
    db.delete(inv)
    db.commit()
    return {"ok": True}


INVOICE_COLUMNS = [
    "发票号码",
    "方向",
    "往来方",
    "关联单据号",
    "不含税金额",
    "税率(%)",
    "税额",
    "价税合计",
    "开票日期",
    "备注",
]

_TEMPLATE_SAMPLE = [["01234567", "销项", "示例客户", "SO2026001", 1000, 13, 130, 1130, "2026-11-06", "示例"]]


def _parse_direction(value) -> str:
    text = str(value or "").strip()
    if text in ("进项", "purchase", "采购", "进项发票"):
        return "purchase"
    return "sales"


def _parse_date(value):
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法识别的日期：{value}")


@router.get("/template", dependencies=[Depends(require_perm("invoice:view"))])
def invoice_template():
    content = importer.build_template(INVOICE_COLUMNS, _TEMPLATE_SAMPLE, sheet_name="发票导入")
    return Response(
        content=content,
        media_type=importer.XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": "attachment; filename*=UTF-8''invoice_import_template.xlsx"},
    )


@router.post("/import", dependencies=[Depends(require_perm("invoice:manage"))])
def import_invoices(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        rows = importer.parse_workbook(file.file.read(), INVOICE_COLUMNS)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    created = 0
    errors: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        lineno = row["_row"]
        try:
            invoice_no = str(row.get("发票号码") or "").strip()
            if not invoice_no:
                raise ValueError("发票号码为空")
            if invoice_no in seen:
                raise ValueError("文件内发票号码重复")
            if db.scalar(select(Invoice.id).where(Invoice.invoice_no == invoice_no)):
                raise ValueError("发票号码已存在")
            seen.add(invoice_no)

            db.add(
                Invoice(
                    invoice_no=invoice_no,
                    direction=_parse_direction(row.get("方向")),
                    partner_name=str(row.get("往来方") or "").strip(),
                    order_no=str(row.get("关联单据号") or "").strip(),
                    amount=importer.parse_number(row.get("不含税金额")),
                    tax_rate=importer.parse_number(row.get("税率(%)")),
                    tax_amount=importer.parse_number(row.get("税额")),
                    total_amount=importer.parse_number(row.get("价税合计")),
                    issue_date=_parse_date(row.get("开票日期")) or datetime.now(),
                    remark=str(row.get("备注") or "").strip(),
                    created_by=user.username,
                )
            )
            created += 1
        except ValueError as exc:
            errors.append({"row": lineno, "reason": str(exc)})

    db.commit()
    return {"created": created, "skipped": len(errors), "errors": errors}


@router.post("/batch-delete", dependencies=[Depends(require_perm("invoice:manage"))])
def batch_delete_invoices(payload: dict, db: Session = Depends(get_db)):
    raw_ids = payload.get("ids") if isinstance(payload, dict) else None
    if not raw_ids:
        raise HTTPException(status_code=400, detail="请先选择要删除的发票")
    ids: list[int] = []
    for value in raw_ids:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        raise HTTPException(status_code=400, detail="参数不合法")

    deleted = 0
    for invoice_id in ids:
        inv = db.get(Invoice, invoice_id)
        if inv:
            db.delete(inv)
            deleted += 1
    db.commit()
    return {"deleted": deleted}


@router.post("/batch-update", dependencies=[Depends(require_perm("invoice:manage"))])
def batch_update_invoices(payload: dict, db: Session = Depends(get_db)):
    raw_ids = payload.get("ids") if isinstance(payload, dict) else None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        data = {}
    if not raw_ids:
        raise HTTPException(status_code=400, detail="请先选择要编辑的发票")
    ids: list[int] = []
    for value in raw_ids:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        raise HTTPException(status_code=400, detail="参数不合法")

    fields: dict = {}
    if data.get("direction") in ("sales", "purchase"):
        fields["direction"] = data["direction"]
    if data.get("tax_rate") is not None and str(data["tax_rate"]).strip() != "":
        try:
            fields["tax_rate"] = round(float(data["tax_rate"]), 2)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="税率不合法")
    if data.get("issue_date") is not None and str(data["issue_date"]).strip() != "":
        try:
            fields["issue_date"] = _parse_date(data["issue_date"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if data.get("remark") is not None:
        fields["remark"] = str(data["remark"]).strip()

    if not fields:
        raise HTTPException(status_code=400, detail="没有可更新的字段")

    updated = 0
    for invoice_id in ids:
        inv = db.get(Invoice, invoice_id)
        if not inv:
            continue
        for key, value in fields.items():
            setattr(inv, key, value)
        if "tax_rate" in fields:
            inv.tax_amount = round(inv.amount * inv.tax_rate / 100, 2)
            inv.total_amount = round(inv.amount + inv.tax_amount, 2)
        updated += 1
    db.commit()
    return {"updated": updated}


_EXPORT_COLUMNS = [
    "发票号码",
    "方向",
    "往来方",
    "关联单据号",
    "不含税金额",
    "税率(%)",
    "税额",
    "价税合计",
    "状态",
    "认证",
    "开票日期",
    "备注",
]

_DIRECTION_LABELS = {"sales": "销项发票", "purchase": "进项发票"}


def _status_label(inv: Invoice) -> str:
    if inv.status == "voided":
        return "已作废"
    if inv.status == "issued":
        return "已收票" if inv.direction == "purchase" else "已开票"
    return "未收票" if inv.direction == "purchase" else "未开票"


def _date_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


@router.post("/batch-export", dependencies=[Depends(require_perm("invoice:view"))])
def batch_export_invoices(payload: dict, db: Session = Depends(get_db)):
    raw_ids = payload.get("ids") if isinstance(payload, dict) else None
    if not raw_ids:
        raise HTTPException(status_code=400, detail="请先选择要导出的发票")
    ids: list[int] = []
    for value in raw_ids:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        raise HTTPException(status_code=400, detail="参数不合法")

    rows = db.scalars(
        select(Invoice)
        .where(Invoice.id.in_(ids))
        .order_by(Invoice.issue_date.desc(), Invoice.id.desc())
    ).all()
    if not rows:
        raise HTTPException(status_code=400, detail="没有可导出的发票")

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "发票"
    sheet.append(_EXPORT_COLUMNS)
    header_fill = PatternFill("solid", fgColor="E8EEF9")
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for inv in rows:
        sheet.append(
            [
                inv.invoice_no,
                _DIRECTION_LABELS.get(inv.direction, inv.direction),
                inv.partner_name,
                inv.order_no,
                inv.amount,
                inv.tax_rate,
                inv.tax_amount,
                inv.total_amount,
                _status_label(inv),
                "已认证" if inv.certified else "",
                _date_str(inv.issue_date),
                inv.remark,
            ]
        )

    for index, width in enumerate((16, 12, 20, 16, 14, 10, 14, 14, 10, 8, 14, 24), start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width

    buffer = io.BytesIO()
    workbook.save(buffer)
    filename = quote(f"发票导出_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
    return Response(
        content=buffer.getvalue(),
        media_type=importer.XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


def _build_invoices_docx(rows: list[Invoice]) -> bytes:
    """把多张发票拼成一个 Word 文档，每张一页，方便一次性打印。"""
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Cm(1.8)
    section.bottom_margin = Cm(1.8)
    section.left_margin = Cm(2)
    section.right_margin = Cm(2)
    normal = doc.styles["Normal"]
    normal.font.name = "宋体"
    normal.font.size = Pt(10.5)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    for index, inv in enumerate(rows):
        if index:
            doc.add_page_break()

        title = doc.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title_run = title.add_run(_DIRECTION_LABELS.get(inv.direction, "发票"))
        title_run.bold = True
        title_run.font.size = Pt(20)

        pairs = [
            ("发票号码", inv.invoice_no),
            ("方向", _DIRECTION_LABELS.get(inv.direction, inv.direction)),
            ("往来方", inv.partner_name),
            ("关联单据号", inv.order_no or ""),
            ("开票日期", _date_str(inv.issue_date)),
            ("状态", _status_label(inv)),
            ("认证", "已认证" if inv.certified else "未认证"),
            ("制单人", inv.created_by or ""),
        ]
        table = doc.add_table(rows=(len(pairs) + 1) // 2, cols=4)
        table.style = "Table Grid"
        table.autofit = True
        for i, (label, value) in enumerate(pairs):
            row = table.rows[i // 2]
            label_cell = row.cells[(i % 2) * 2]
            value_cell = row.cells[(i % 2) * 2 + 1]
            label_cell.text = ""
            label_run = label_cell.paragraphs[0].add_run(f"{label}：")
            label_run.bold = True
            value_cell.text = ""
            value_cell.paragraphs[0].add_run("" if value is None else str(value))
        if len(pairs) % 2:
            table.rows[-1].cells[2].text = ""
            table.rows[-1].cells[3].text = ""

        doc.add_paragraph()
        for label, value, prefix in (
            ("不含税金额", money(inv.amount), "¥ "),
            ("税率", f"{inv.tax_rate}%", ""),
            ("税额", money(inv.tax_amount), "¥ "),
            ("价税合计", money(inv.total_amount), "¥ "),
        ):
            paragraph = doc.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            run = paragraph.add_run(f"{label}：{prefix}{value}")
            run.bold = label == "价税合计"

        if inv.remark:
            doc.add_paragraph(f"备注：{inv.remark}")

    return doc_to_bytes(doc)


@router.post("/batch-print", dependencies=[Depends(require_perm("invoice:view"))])
def batch_print_invoices(payload: dict, db: Session = Depends(get_db)):
    raw_ids = payload.get("ids") if isinstance(payload, dict) else None
    if not raw_ids:
        raise HTTPException(status_code=400, detail="请先选择要打印的发票")
    ids: list[int] = []
    for value in raw_ids:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        raise HTTPException(status_code=400, detail="参数不合法")

    rows = db.scalars(
        select(Invoice)
        .where(Invoice.id.in_(ids))
        .order_by(Invoice.issue_date.desc(), Invoice.id.desc())
    ).all()
    if not rows:
        raise HTTPException(status_code=400, detail="没有可打印的发票")

    content = _build_invoices_docx(rows)
    filename = quote(f"发票打印_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx")
    return docx_response(content, filename)


@router.post("/batch-review", dependencies=[Depends(require_perm("invoice:manage"))])
def batch_review_invoices(
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    raw_ids = payload.get("ids") if isinstance(payload, dict) else None
    if not raw_ids:
        raise HTTPException(status_code=400, detail="请先选择要审核的发票")
    ids: list[int] = []
    for value in raw_ids:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        raise HTTPException(status_code=400, detail="参数不合法")

    reviewed = 0
    now = datetime.now()
    for invoice_id in ids:
        inv = db.get(Invoice, invoice_id)
        if not inv or inv.reviewed:
            continue
        inv.reviewed = True
        inv.reviewed_by = user.username
        inv.reviewed_at = now
        reviewed += 1
    db.commit()
    return {"reviewed": reviewed}