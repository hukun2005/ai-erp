"""Excel 批量导入：模板生成、解析与逐行报错。

两条边界：
1. 只导入主数据。库存一律走 services.stock 写台账，避免「商品总库存」与「库位余额」
   出现两套对不上的账；
2. 单元格错误只影响该行，其余行照常入库，并逐行返回原因，让用户改完再传一次。
"""
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

MAX_IMPORT_ROWS = 2000
MAX_IMPORT_BYTES = 5 * 1024 * 1024

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def build_template(columns: list[str], sample: list[list], sheet_name: str = "导入模板") -> bytes:
    """生成带表头样式与示例行的 xlsx 模板。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    sheet.append(columns)
    fill = PatternFill("solid", fgColor="E8EEF9")
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center")
    for row in sample:
        sheet.append(row)
    for index in range(1, len(columns) + 1):
        sheet.column_dimensions[get_column_letter(index)].width = 18
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def parse_workbook(content: bytes, columns: list[str]) -> list[dict]:
    """解析上传的 xlsx，返回 [{"_row": 行号, 列名: 值}]。

    表头顺序不限，但列名必须齐全；空行自动跳过。
    """
    if not content:
        raise ValueError("文件为空，请先下载模板填写后上传")
    if len(content) > MAX_IMPORT_BYTES:
        raise ValueError("文件超过 5MB，请拆分后分批导入")

    try:
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 非法文件统一转成可读提示
        raise ValueError("无法解析文件，请使用下载的 .xlsx 模板") from exc

    iterator = workbook.active.iter_rows(values_only=True)
    header = next(iterator, None)
    if not header:
        raise ValueError("文件为空，请先下载模板")
    names = [str(cell).strip() if cell is not None else "" for cell in header]
    missing = [name for name in columns if name not in names]
    if missing:
        raise ValueError(f"表头缺少列：{'、'.join(missing)}")
    positions = {name: names.index(name) for name in columns}

    rows: list[dict] = []
    for offset, raw in enumerate(iterator, start=2):
        if raw is None or all(cell is None or str(cell).strip() == "" for cell in raw):
            continue
        if len(rows) >= MAX_IMPORT_ROWS:
            raise ValueError(f"单次最多导入 {MAX_IMPORT_ROWS} 行，请拆分文件后分批导入")
        rows.append(
            {
                "_row": offset,
                **{
                    name: raw[positions[name]] if positions[name] < len(raw) else None
                    for name in columns
                },
            }
        )
    if not rows:
        raise ValueError("文件里没有有效数据行")
    return rows


def parse_number(value, default: float = 0.0) -> float:
    """把单元格转成金额；空值取默认值，非法内容直接报错。"""
    if value is None or str(value).strip() == "":
        return default
    try:
        return round(float(value), 2)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"数值不合法：{value}") from exc


PRODUCT_TYPE_LABELS = {"raw_material": "原料", "finished_good": "成品"}


def parse_product_type(value) -> str:
    """把「原料/成品」或英文枚举归一化成数据库取值；空值按成品处理。"""
    text = str(value or "").strip()
    if not text:
        return "finished_good"
    if text in ("原料", "raw_material"):
        return "raw_material"
    if text in ("成品", "finished_good"):
        return "finished_good"
    raise ValueError("物料类型只能是 原料/成品")


def parse_int(value, default: int = 0) -> int:
    """把单元格转成整数；允许 12.0 这类浮点写法。"""
    if value is None or str(value).strip() == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"数量不合法：{value}") from exc
    if number != int(number):
        raise ValueError(f"数量必须是整数：{value}")
    return int(number)


def build_error_report(errors: list[dict], columns: list[str]) -> bytes:
    """把逐行校验错误导出为 xlsx：行号 + 原始数据 + 原因，方便改完再传。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "校验错误"
    header = ["行号", *columns, "错误原因"]
    sheet.append(header)
    fill = PatternFill("solid", fgColor="FDE68A")
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center")
    for err in errors:
        data = err.get("data") or {}
        sheet.append([err.get("row"), *[data.get(col) for col in columns], err.get("reason")])
    for index in range(1, len(header) + 1):
        sheet.column_dimensions[get_column_letter(index)].width = 18
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
