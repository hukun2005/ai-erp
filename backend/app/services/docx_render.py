"""Word 单据渲染。

客户上传自己的 .docx 初始格式，文件里用 {{占位符}} 标记要填的数据；
打印时把订单数据填进去，输出一个真正可编辑、可打印的 Word 文件。

占位符约定（销售订单）：
- 单头：{{order_no}}、{{order_date}}、{{customer_name}} 等，写在段落或表格单元格里
- 明细：把 {{item.name}} 等写进表格的同一行，系统按明细行数复制该行

未上传模板（或模板文件丢失）时，使用本模块内置的默认版式，保证打印始终可用。
"""
import re
from copy import deepcopy
from io import BytesIO
from urllib.parse import quote

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from docx.table import _Row
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DocumentTemplate
from .files import upload_path

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")
ITEM_KEYS = (
    "no", "sku", "name", "spec", "unit", "category",
    "quantity", "unit_price", "amount", "batch_no", "remark",
)
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def money(value) -> str:
    return f"{float(value or 0):,.2f}"


def pick_word_template(db: Session, doc_type: str, template_id: int | None = None) -> DocumentTemplate | None:
    """优先用指定模板；否则用该类单据默认且已上传 Word 文件的模板。"""
    if template_id:
        tpl = db.get(DocumentTemplate, template_id)
        if tpl and tpl.doc_type == doc_type and tpl.enabled and tpl.word_file:
            return tpl
    return db.scalar(
        select(DocumentTemplate)
        .where(DocumentTemplate.doc_type == doc_type)
        .where(DocumentTemplate.word_file != "")
        .where(DocumentTemplate.enabled == True)  # noqa: E712
        .order_by(DocumentTemplate.is_default.desc(), DocumentTemplate.id.desc())
    )


def docx_response(data: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        BytesIO(data),
        media_type=DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def build_docx_download(
    db: Session,
    doc_type: str,
    template_id: int | None,
    values: dict,
    items: list[dict],
    default_builder,
    default_title: str,
    doc_no: str,
) -> StreamingResponse:
    """三类单据共用的下载流程：套客户模板 → 没有则用内置版式。"""
    template = pick_word_template(db, doc_type, template_id)
    config = (template.config or {}) if template else {}
    company = config.get("company", "") or ""
    title = (template.title if template else "") or default_title
    values["company"] = company

    path = upload_path(template.word_file) if template else None
    if path and path.is_file():
        data = render_docx_template(path, values, items)
    else:
        data = default_builder(values, items, company=company, title=title)

    filename = f"{title}_{doc_no}.docx".replace("/", "_").replace("\\", "_")
    return docx_response(data, filename)


# ---------------- 占位符替换 ----------------
def _run_offsets(paragraph):
    starts, lengths = [], []
    position = 0
    for run in paragraph.runs:
        starts.append(position)
        lengths.append(len(run.text))
        position += len(run.text)
    return starts, lengths


def _index_at(starts, lengths, index):
    """找到覆盖指定字符位置的 run，兼容占位符被 Word 拆成多个 run 的情况。"""
    for i, (start, length) in enumerate(zip(starts, lengths)):
        if length and start <= index < start + length:
            return i
    for i in range(len(lengths) - 1, -1, -1):
        if lengths[i] and starts[i] <= index:
            return i
    return None


def _splice(paragraph, match, replacement):
    runs = paragraph.runs
    if not runs:
        return
    starts, lengths = _run_offsets(paragraph)
    first = _index_at(starts, lengths, match.start())
    last = _index_at(starts, lengths, match.end() - 1)
    if first is None or last is None:
        return
    if first > last:
        first, last = last, first
    prefix = runs[first].text[: match.start() - starts[first]]
    suffix = runs[last].text[match.end() - starts[last]:]
    # 保留起始 run 的格式，跨 run 时把后续 run 的文本清空
    runs[first].text = prefix + replacement + suffix
    for run in runs[first + 1: last + 1]:
        run.text = ""


def replace_placeholders(paragraph, values):
    """替换一个段落内的全部已知占位符；未知占位符保留原样，方便客户发现拼写错误。"""
    while True:
        text = "".join(run.text for run in paragraph.runs)
        matches = [m for m in PLACEHOLDER_RE.finditer(text) if m.group(1) in values]
        if not matches:
            return
        match = matches[-1]  # 从右往左替换，前面匹配的位置不会失效
        value = values[match.group(1)]
        _splice(paragraph, match, "" if value is None else str(value))


def _iter_paragraphs(container):
    for paragraph in container.paragraphs:
        yield paragraph
    for table in container.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from _iter_paragraphs(cell)


def _all_paragraphs(doc):
    yield from _iter_paragraphs(doc)
    for section in doc.sections:
        yield from _iter_paragraphs(section.header)
        yield from _iter_paragraphs(section.footer)


# ---------------- 明细行循环 ----------------
def _row_has_placeholders(row, prefix="{{item."):
    return any(prefix in paragraph.text for cell in row.cells for paragraph in cell.paragraphs)


def _expand_table(table, items):
    for row in list(table.rows):
        if not _row_has_placeholders(row):
            continue
        template_tr = row._tr
        anchor = template_tr
        for index, item in enumerate(items):
            new_tr = deepcopy(template_tr)
            anchor.addnext(new_tr)
            anchor = new_tr
            new_row = _Row(new_tr, table)
            values = {f"item.{key}": item.get(key, "") for key in ITEM_KEYS}
            values["item.no"] = index + 1
            for cell in new_row.cells:
                for paragraph in cell.paragraphs:
                    replace_placeholders(paragraph, values)
        # 明细为空时只保留表头，不留下模板行
        template_tr.getparent().remove(template_tr)
        break  # 一张表只按第一个含明细占位符的行扩展


def _expand_tables(container, items):
    for table in container.tables:
        _expand_table(table, items)
        for row in table.rows:
            for cell in row.cells:
                _expand_tables(cell, items)


# ---------------- 对外：渲染上传的模板 ----------------
def render_docx_template(source, values, items):
    """source 可以是文件路径或二进制流；返回渲染后的 .docx 字节。"""
    doc = Document(source)
    _expand_tables(doc, items)
    # 明细占位符如果被误写到表格外，清空而不是把 {{item.xxx}} 印在纸上
    empty_item_values = {f"item.{key}": "" for key in ITEM_KEYS}
    for paragraph in _all_paragraphs(doc):
        replace_placeholders(paragraph, values)
        replace_placeholders(paragraph, empty_item_values)
    return doc_to_bytes(doc)


def doc_to_bytes(doc):
    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


# ---------------- 内置默认版式 ----------------
def _new_document():
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
    return doc


def _centered(doc, text, size=11, bold=False):
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run(str(text))
    run.bold = bold
    run.font.size = Pt(size)
    return paragraph


def _meta_table(doc, pairs):
    table = doc.add_table(rows=(len(pairs) + 1) // 2, cols=4)
    table.style = "Table Grid"
    table.autofit = True
    for index, (label, value) in enumerate(pairs):
        row = table.rows[index // 2]
        label_cell = row.cells[(index % 2) * 2]
        value_cell = row.cells[(index % 2) * 2 + 1]
        label_cell.text = ""
        label_run = label_cell.paragraphs[0].add_run(f"{label}：")
        label_run.bold = True
        value_cell.text = ""
        value_cell.paragraphs[0].add_run("" if value is None else str(value))
    # 占位表格补空，避免出现没有内容的单元格
    if len(pairs) % 2:
        row = table.rows[-1]
        row.cells[2].text = ""
        row.cells[3].text = ""
    return table


SALES_COLUMNS = [
    ("序号", "no", "center"),
    ("商品编码", "sku", "left"),
    ("商品名称", "name", "left"),
    ("规格", "spec", "left"),
    ("单位", "unit", "center"),
    ("数量", "quantity", "right"),
    ("单价", "unit_price", "right"),
    ("金额", "amount", "right"),
]
INBOUND_COLUMNS = SALES_COLUMNS + [("批次号", "batch_no", "left"), ("备注", "remark", "left")]
ALIGN_MAP = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
}


def _items_table(doc, items, columns=None):
    columns = columns or SALES_COLUMNS
    table = doc.add_table(rows=1, cols=len(columns))
    table.style = "Table Grid"
    table.autofit = True
    for cell, (header, _, _) in zip(table.rows[0].cells, columns):
        cell.text = ""
        run = cell.paragraphs[0].add_run(header)
        run.bold = True
        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    for index, item in enumerate(items):
        cells = table.add_row().cells
        for cell, (_, key, align) in zip(cells, columns):
            cell.text = ""
            paragraph = cell.paragraphs[0]
            value = index + 1 if key == "no" else item.get(key, "")
            paragraph.add_run(str(value))
            paragraph.alignment = ALIGN_MAP.get(align, WD_ALIGN_PARAGRAPH.LEFT)
    return table


def build_default_document(
    values,
    items,
    company="",
    title="销售订单",
    signs=("制单人", "审核人", "客户签收"),
    meta_pairs=None,
    item_columns=None,
    totals=None,
):
    """内置默认版式：客户没上传模板、或模板文件缺失时兜底。"""
    doc = _new_document()
    _centered(doc, title, size=20, bold=True)
    if company:
        _centered(doc, company, size=10.5)

    if meta_pairs is None:
        meta_pairs = [
            ("单据编号", values.get("order_no", "")),
            ("下单时间", values.get("order_date", "")),
            ("客户", values.get("customer_name", "")),
            ("联系人", values.get("customer_contact", "")),
            ("联系电话", values.get("customer_phone", "")),
            ("单据状态", values.get("status_text", "")),
            ("制单人", values.get("created_by", "")),
            ("客户地址", values.get("customer_address", "")),
        ]
    _meta_table(doc, meta_pairs)

    doc.add_paragraph()
    _items_table(doc, items, item_columns)

    if totals is None:
        totals = [
            ("商品金额", values.get("gross_amount", "")),
            ("优惠金额", values.get("discount_amount", "")),
            ("应收合计", values.get("total_amount", "")),
        ]
    doc.add_paragraph()
    for label, value in totals:
        paragraph = doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        run = paragraph.add_run(f"{label}：¥ {value}")
        run.bold = label in ("应收合计", "采购合计", "合计金额")

    if values.get("remark"):
        doc.add_paragraph()
        doc.add_paragraph(f"备注：{values['remark']}")

    sign = doc.add_paragraph()
    sign.paragraph_format.space_before = Pt(30)
    sign.add_run("    ".join(f"{label}：____________" for label in signs))
    return doc_to_bytes(doc)


def build_purchase_document(values, items, company="", title="采购订单"):
    meta_pairs = [
        ("采购单号", values.get("order_no", "")),
        ("下单时间", values.get("order_date", "")),
        ("供应商", values.get("supplier_name", "")),
        ("联系人", values.get("supplier_contact", "")),
        ("联系电话", values.get("supplier_phone", "")),
        ("单据状态", values.get("status_text", "")),
        ("制单人", values.get("created_by", "")),
        ("供应商地址", values.get("supplier_address", "")),
    ]
    return build_default_document(
        values, items, company=company, title=title,
        signs=("制单人", "审核人", "供应商确认"),
        meta_pairs=meta_pairs,
        item_columns=SALES_COLUMNS,
        totals=[("采购合计", values.get("total_amount", ""))],
    )


def build_inbound_document(values, items, company="", title="入库单"):
    meta_pairs = [
        ("入库单号", values.get("order_no", "")),
        ("入库日期", values.get("order_date", "")),
        ("供应商", values.get("supplier_name", "")),
        ("联系人", values.get("supplier_contact", "")),
        ("联系电话", values.get("supplier_phone", "")),
        ("入库仓库", values.get("warehouse_name", "")),
        ("库位", values.get("location_code", "")),
        ("关联采购单", values.get("purchase_order_no", "")),
        ("单据状态", values.get("status_text", "")),
        ("制单人", values.get("created_by", "")),
    ]
    return build_default_document(
        values, items, company=company, title=title,
        signs=("制单人", "仓管员", "送货人"),
        meta_pairs=meta_pairs,
        item_columns=INBOUND_COLUMNS,
        totals=[("合计金额", values.get("total_amount", ""))],
    )


SAMPLE_VALUES = {
    "order_no": "{{order_no}}",
    "order_date": "{{order_date}}",
    "status_text": "{{status_text}}",
    "created_by": "{{created_by}}",
    "customer_name": "{{customer_name}}",
    "customer_contact": "{{customer_contact}}",
    "customer_phone": "{{customer_phone}}",
    "customer_address": "{{customer_address}}",
    "gross_amount": "{{gross_amount}}",
    "discount_amount": "{{discount_amount}}",
    "total_amount": "{{total_amount}}",
    "remark": "{{remark}}",
}
SAMPLE_ITEM = {
    "no": "{{item.no}}",
    "sku": "{{item.sku}}",
    "name": "{{item.name}}",
    "spec": "{{item.spec}}",
    "unit": "{{item.unit}}",
    "quantity": "{{item.quantity}}",
    "unit_price": "{{item.unit_price}}",
    "amount": "{{item.amount}}",
}
SAMPLE_INBOUND_ITEM = {**SAMPLE_ITEM, "batch_no": "{{item.batch_no}}", "remark": "{{item.remark}}"}
SAMPLE_PURCHASE_VALUES = {
    "order_no": "{{order_no}}",
    "order_date": "{{order_date}}",
    "status_text": "{{status_text}}",
    "created_by": "{{created_by}}",
    "supplier_name": "{{supplier_name}}",
    "supplier_contact": "{{supplier_contact}}",
    "supplier_phone": "{{supplier_phone}}",
    "supplier_address": "{{supplier_address}}",
    "total_amount": "{{total_amount}}",
    "remark": "{{remark}}",
}
SAMPLE_INBOUND_VALUES = {
    "order_no": "{{order_no}}",
    "order_date": "{{order_date}}",
    "status_text": "{{status_text}}",
    "created_by": "{{created_by}}",
    "supplier_name": "{{supplier_name}}",
    "supplier_contact": "{{supplier_contact}}",
    "supplier_phone": "{{supplier_phone}}",
    "warehouse_name": "{{warehouse_name}}",
    "location_code": "{{location_code}}",
    "purchase_order_no": "{{purchase_order_no}}",
    "total_amount": "{{total_amount}}",
    "remark": "{{remark}}",
}


def build_sample_document(doc_type="sales_order"):
    """示例/初始格式模板：客户下载后可直接在 Word 里改成自己的版式。"""
    if doc_type == "purchase_order":
        return build_purchase_document(
            SAMPLE_PURCHASE_VALUES, [dict(SAMPLE_ITEM)], company="{{company}}", title="采购订单"
        )
    if doc_type == "inbound":
        return build_inbound_document(
            SAMPLE_INBOUND_VALUES, [dict(SAMPLE_INBOUND_ITEM)], company="{{company}}", title="入库单"
        )
    return build_default_document(
        SAMPLE_VALUES, [dict(SAMPLE_ITEM)], company="{{company}}", title="销售订单"
    )
