"""服务端 PDF 单据渲染。

用 reportlab 直接画版式（不依赖浏览器/Office）：支持中文字体、自动分页、
多联（每联一页并标注联次）、水印、页码、单号条码与套打坐标偏移。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from pypdf import PdfReader, PdfWriter
from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, A5
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

FONT = "STSong-Light"
# 内置 CID 字体，无需随包附带字体文件；文本层可被复制/搜索
pdfmetrics.registerFont(UnicodeCIDFont(FONT))

PAPERS = {"A4": A4, "A5": A5}
WHITE = colors.white
INK = colors.HexColor("#1f2937")
MUTED = colors.HexColor("#6b7280")
LINE = colors.HexColor("#d1d5db")
HEAD_BG = colors.HexColor("#f1f5f9")


@dataclass
class PrintOptions:
    """一次打印请求的版式选项。"""

    paper: str = "A4"
    width_mm: float | None = None
    height_mm: float | None = None
    margin_mm: float = 14.0
    copies: int = 1
    copy_labels: list[str] = field(default_factory=list)
    watermark: str = ""
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0
    show_page_no: bool = True
    barcode: bool = True
    font_size: float = 9.0

    def size(self) -> tuple[float, float]:
        if self.width_mm and self.height_mm:
            return (float(self.width_mm) * mm, float(self.height_mm) * mm)
        return PAPERS.get((self.paper or "A4").upper(), A4)


def _wrap(text: Any, size: float, max_width: float) -> list[str]:
    """按字符宽度折行；中英文混排都能正确断行。"""
    value = "" if text is None else str(text)
    if not value:
        return [""]
    lines: list[str] = []
    current = ""
    for char in value:
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        trial = current + char
        if pdfmetrics.stringWidth(trial, FONT, size) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = char
    lines.append(current)
    return lines


def _money(value: Any) -> str:
    try:
        return f"{float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _resolve_template(template: dict | None) -> dict:
    config = (template or {}).get("config") or {}
    return {
        "title": (template or {}).get("title") or "",
        "company": config.get("company") or "",
        "logo": config.get("logo") if config.get("show_logo") else "",
        "columns": config.get("columns") or {},
        "terms": config.get("terms") or "",
        "signs": config.get("signs") or ["制单", "审核", "客户签收"],
        "paper": config.get("paper") or "",
    }


def _draw_watermark(c: canvas.Canvas, width: float, height: float, text: str) -> None:
    if not text:
        return
    c.saveState()
    try:
        c.setFillAlpha(0.08)
    except Exception:  # noqa: BLE001 老版本 reportlab 不支持透明度时退化为浅灰
        c.setFillColor(colors.HexColor("#e5e7eb"))
    c.setFillColor(colors.HexColor("#9ca3af"))
    c.setFont(FONT, 40)
    c.translate(width / 2, height / 2)
    c.rotate(35)
    c.drawCentredString(0, 0, text[:20])
    c.restoreState()


def _draw_barcode(c: canvas.Canvas, x: float, y: float, value: str) -> float:
    """画单号条码，返回占用高度。"""
    if not value:
        return 0
    barcode = code128.Code128(value, barHeight=10 * mm, barWidth=0.28 * mm, humanReadable=False)
    barcode.drawOn(c, x, y - 10 * mm)
    c.setFont(FONT, 6.5)
    c.setFillColor(MUTED)
    c.drawString(x, y - 13 * mm, value)
    c.setFillColor(INK)
    return 14 * mm


def render_document(
    spec: dict,
    options: PrintOptions | None = None,
    template: dict | None = None,
) -> bytes:
    """把单据数据渲染成 PDF 字节流。

    spec 结构：title / doc_no / meta / columns / rows / totals / remark / signs。
    """
    options = options or PrintOptions()
    tpl = _resolve_template(template)
    columns = spec.get("columns") or []
    # 模板可隐藏列、改列名
    config_columns = tpl["columns"]
    shown = []
    for column in columns:
        key = column.get("key")
        if config_columns.get(key) is False:
            continue
        label = column.get("label", "")
        if isinstance(config_columns.get(key), str):
            label = config_columns[key]
        shown.append({**column, "label": label})
    if not shown:
        shown = columns

    title = spec.get("title") or tpl["title"] or "单据"
    company = tpl["company"] or spec.get("company", "")
    terms = tpl["terms"] or spec.get("terms", "")
    signs = spec.get("signs") or tpl["signs"]
    rows = spec.get("rows") or []
    meta = spec.get("meta") or []

    width, height = options.size()
    margin = options.margin_mm * mm
    offset_x = options.offset_x_mm * mm
    offset_y = options.offset_y_mm * mm
    copies = max(1, min(int(options.copies or 1), 8))
    labels = list(options.copy_labels)[:copies]

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height))
    c.setTitle(f"{title} {spec.get('doc_no', '')}".strip())
    c.setAuthor(company or "AI-ERP")
    total_pages_estimate = max(1, (len(rows) + 15) // 16) * copies

    def page_head(first: bool) -> float:
        """画页眉，返回可用起始 y。"""
        y = height - margin + offset_y
        if options.watermark:
            _draw_watermark(c, width, height, options.watermark)
        c.setFillColor(INK)
        c.setFont(FONT, 16 if first else 12)
        c.drawString(margin + offset_x, y - 16 * mm if first else y - 10 * mm, title)
        if company:
            c.setFont(FONT, 9)
            c.setFillColor(MUTED)
            c.drawRightString(
                width - margin + offset_x,
                y - (16 if first else 10) * mm,
                company,
            )
        c.setFillColor(INK)
        c.setFont(FONT, 8.5)
        c.drawString(margin + offset_x, y - 24 * mm if first else y - 17 * mm, f"单号：{spec.get('doc_no', '')}")
        c.setStrokeColor(LINE)
        c.line(
            margin + offset_x,
            y - (26 if first else 19) * mm,
            width - margin + offset_x,
            y - (26 if first else 19) * mm,
        )
        return y - (30 if first else 23) * mm

    def draw_meta(y: float) -> float:
        if not meta:
            return y
        column_width = (width - 2 * margin) / 2
        size = options.font_size
        for index, (label, value) in enumerate(meta):
            row_index, col_index = divmod(index, 2)
            tx = margin + offset_x + col_index * column_width
            ty = y - row_index * (size + 4) * mm / 1.2
            c.setFont(FONT, size)
            c.setFillColor(MUTED)
            c.drawString(tx, ty, f"{label}：")
            c.setFillColor(INK)
            c.drawString(tx + 22 * mm, ty, str(value or ""))
        rows_used = (len(meta) + 1) // 2
        return y - rows_used * (size + 4) * mm / 1.2 - 3 * mm

    def column_widths() -> list[float]:
        available = width - 2 * margin
        weights = [float(column.get("width") or 1) for column in shown]
        total = sum(weights) or len(weights)
        return [available * weight / total for weight in weights]

    widths = column_widths()
    line_height = options.font_size * 1.55
    bottom_limit = margin + 26 * mm  # 预留签字栏与页码

    def draw_table_header(y: float) -> float:
        c.setFillColor(HEAD_BG)
        c.rect(margin + offset_x, y - line_height, sum(widths), line_height, stroke=0, fill=1)
        c.setFillColor(INK)
        c.setFont(FONT, options.font_size)
        c.setStrokeColor(LINE)
        x = margin + offset_x
        for column, column_width in zip(shown, widths):
            align = column.get("align", "left")
            if align == "right":
                c.drawRightString(x + column_width - 2 * mm, y - line_height + 2.2 * mm, column["label"])
            elif align == "center":
                c.drawCentredString(x + column_width / 2, y - line_height + 2.2 * mm, column["label"])
            else:
                c.drawString(x + 2 * mm, y - line_height + 2.2 * mm, column["label"])
            c.rect(x, y - line_height, column_width, line_height, stroke=1, fill=0)
            x += column_width
        return y - line_height

    state = {"page": 0}

    def new_page(first: bool = False) -> float:
        if state["page"] > 0:
            if options.show_page_no:
                c.setFont(FONT, 7.5)
                c.setFillColor(MUTED)
                c.drawCentredString(
                    width / 2,
                    margin / 2 + offset_y,
                    f"第 {state['page']} 页 / 共约 {total_pages_estimate} 页",
                )
            c.showPage()
        state["page"] += 1
        return page_head(first)

    # ── 每份副本一页起，标注联次 ──
    for copy_index in range(copies):
        y = new_page(first=True)
        if labels and copy_index < len(labels):
            c.setFont(FONT, 9)
            c.setFillColor(colors.HexColor("#b91c1c"))
            c.drawString(margin + offset_x, y + 12 * mm, f"【{labels[copy_index]}】")
            c.setFillColor(INK)
        if options.barcode and spec.get("doc_no"):
            _draw_barcode(c, width - margin + offset_x - 45 * mm, y + 4 * mm, str(spec.get("doc_no")))
        y = draw_meta(y)
        y = draw_table_header(y)

        for row in rows:
            # 逐行折行，先算这一行高度
            cell_lines = []
            max_lines = 1
            for column, column_width in zip(shown, widths):
                raw = row.get(column.get("key"), "")
                if column.get("format") == "money":
                    raw = _money(raw)
                lines = _wrap(raw, options.font_size, column_width - 4 * mm)
                cell_lines.append(lines)
                max_lines = max(max_lines, len(lines))
            row_height = line_height * max_lines

            if y - row_height < bottom_limit:
                y = new_page(first=False)
                y = draw_table_header(y)

            x = margin + offset_x
            for column, column_width, lines in zip(shown, widths, cell_lines):
                align = column.get("align", "left")
                for line_index, line in enumerate(lines):
                    ty = y - line_height + 2.2 * mm - line_index * line_height
                    if align == "right":
                        c.drawRightString(x + column_width - 2 * mm, ty, line)
                    elif align == "center":
                        c.drawCentredString(x + column_width / 2, ty, line)
                    else:
                        c.drawString(x + 2 * mm, ty, line)
                c.rect(x, y - row_height, column_width, row_height, stroke=1, fill=0)
                x += column_width
            y -= row_height

        if not rows:
            c.setFont(FONT, options.font_size)
            c.setFillColor(MUTED)
            c.drawCentredString(width / 2, y - line_height, "无明细")
            c.setFillColor(INK)
            y -= line_height

        for label, value, bold in spec.get("totals") or []:
            c.setFont(FONT, 12 if bold else options.font_size + 0.5)
            c.drawRightString(width - margin + offset_x - 46 * mm, y - 6 * mm, f"{label}：")
            c.drawRightString(width - margin + offset_x, y - 6 * mm, str(value))
            y -= 7 * mm

        if spec.get("remark"):
            y -= 2 * mm
            c.setFont(FONT, options.font_size)
            c.setFillColor(MUTED)
            for line in _wrap(f"备注：{spec['remark']}", options.font_size, width - 2 * margin):
                c.drawString(margin + offset_x, y - 5 * mm, line)
                y -= 5 * mm
        if terms:
            y -= 2 * mm
            c.setFont(FONT, 7.5)
            c.setFillColor(MUTED)
            for line in _wrap(terms, 7.5, width - 2 * margin):
                c.drawString(margin + offset_x, y - 4 * mm, line)
                y -= 4 * mm

        # 签字栏固定在页面底部，避免被明细推走
        sign_y = margin + 12 * mm + offset_y
        c.setStrokeColor(LINE)
        c.setFillColor(MUTED)
        c.setFont(FONT, options.font_size)
        sign_count = max(len(signs), 1)
        sign_width = (width - 2 * margin) / sign_count
        for index, label in enumerate(signs):
            sx = margin + offset_x + index * sign_width
            c.line(sx + 4 * mm, sign_y, sx + sign_width - 4 * mm, sign_y)
            c.drawCentredString(sx + sign_width / 2, sign_y - 5 * mm, label)

    if options.show_page_no:
        c.setFont(FONT, 7.5)
        c.setFillColor(MUTED)
        c.drawCentredString(
            width / 2,
            margin / 2 + offset_y,
            f"第 {state['page']} 页 / 共 {state['page']} 页",
        )
    c.showPage()
    c.save()
    return buffer.getvalue()


def merge_pdfs(documents: list[bytes]) -> bytes:
    """把多张单据的 PDF 合并成一个文件，用于批量打印。"""
    writer = PdfWriter()
    for data in documents:
        reader = PdfReader(BytesIO(data))
        for page in reader.pages:
            writer.add_page(page)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def calibration_sheet(offset_x_mm: float = 0, offset_y_mm: float = 0) -> bytes:
    """套打校准页：10mm 网格 + 十字标尺，配合打印机微调偏移量。"""
    width, height = A4
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    c.setFont(FONT, 12)
    c.drawString(20 * mm, height - 18 * mm, "AI-ERP 套打校准页（241×140 预印纸请选自定义纸张）")
    c.setFont(FONT, 9)
    c.drawString(20 * mm, height - 25 * mm, f"当前偏移：X={offset_x_mm}mm  Y={offset_y_mm}mm")
    c.setStrokeColor(colors.HexColor("#cbd5e1"))
    step = 10 * mm
    x = 0
    while x <= width:
        c.line(x, 0, x, height)
        x += step
    y = 0
    while y <= height:
        c.line(0, y, width, y)
        y += step
    c.setStrokeColor(colors.HexColor("#b91c1c"))
    c.setLineWidth(1.2)
    origin_x, origin_y = 20 * mm + offset_x_mm * mm, 20 * mm + offset_y_mm * mm
    c.line(origin_x - 8 * mm, origin_y, origin_x + 8 * mm, origin_y)
    c.line(origin_x, origin_y - 8 * mm, origin_x, origin_y + 8 * mm)
    c.setFont(FONT, 8)
    c.drawString(origin_x + 3 * mm, origin_y + 2 * mm, "打印原点 (20,20)")
    c.showPage()
    c.save()
    return buffer.getvalue()
