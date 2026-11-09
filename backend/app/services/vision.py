"""视觉识别：把送货单/采购单/客户订单照片转成结构化草稿。

模型只负责「看图转结构化」，商品与往来单位的匹配由 matching 模块按名称/别名
完成，避免把不存在的主数据直接落库。照片会先在本地转正、压缩再上传：手机原图
动辄数 MB，直接 base64 上传既慢又容易触发模型侧的图片大小限制。
"""
from __future__ import annotations

import base64
import io
import json
import re
import time
from dataclasses import dataclass

import httpx
from fastapi import HTTPException
from PIL import Image, ImageOps
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Customer, Product, Supplier
from . import matching

# 单张图片解码上限，避免超大扫描件把进程内存打满
Image.MAX_IMAGE_PIXELS = 60_000_000

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
RETRY_DELAY_SECONDS = 1.2
# 单次模型调用的最多尝试次数：限流或连接失败时重试一次
MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class DocSpec:
    """一种可识别单据的字段约定。"""

    key: str
    label: str
    partner_kind: str  # supplier / customer
    partner_field: str  # supplier_name / customer_name
    date_field: str  # receipt_date / order_date
    product_type: str  # 明细只在这类商品里匹配
    permission: str
    header_hint: str


DOC_SPECS: dict[str, DocSpec] = {
    "inbound": DocSpec(
        key="inbound",
        label="采购入库单/送货单",
        partner_kind="supplier",
        partner_field="supplier_name",
        date_field="receipt_date",
        product_type="raw_material",
        permission="inbound:manage",
        header_hint='{"supplier_name":"供应商名称","doc_no":"单据号","receipt_date":"YYYY-MM-DD"}',
    ),
    "purchase": DocSpec(
        key="purchase",
        label="供应商报价单/采购订单",
        partner_kind="supplier",
        partner_field="supplier_name",
        date_field="order_date",
        product_type="raw_material",
        permission="purchase:manage",
        header_hint=(
            '{"supplier_name":"供应商名称","doc_no":"单据号",'
            '"order_date":"YYYY-MM-DD","delivery_date":"YYYY-MM-DD"}'
        ),
    ),
    "sales": DocSpec(
        key="sales",
        label="客户订单/订货单",
        partner_kind="customer",
        partner_field="customer_name",
        date_field="order_date",
        product_type="finished_good",
        permission="sales:manage",
        header_hint=(
            '{"customer_name":"客户名称","doc_no":"单据号","order_date":"YYYY-MM-DD",'
            '"contact":"联系人","address":"收货地址"}'
        ),
    ),
}

ITEM_SCHEMA = (
    '{"name":"品名","spec":"规格型号","unit":"单位","sku":"物料编码或货号",'
    '"quantity":0,"unit_price":0,"amount":0,"remark":""}'
)


def build_prompt(spec: DocSpec) -> str:
    """按单据类型生成识别提示词：字段少而变化多，放这里比散在调用处清晰。"""
    return f"""你是{spec.label}识别助手，把单据照片转成结构化 JSON，供 ERP 自动填充草稿。

只输出一个 JSON 对象，不要 Markdown 代码块，不要解释，不要输出思考过程。
结构如下（items 为明细数组）：
{{{spec.header_hint},"total_amount":0,"items":[{ITEM_SCHEMA}],"uncertain":[]}}

规则：
1. 只读取图上的信息，不猜测、不补全；看不清的字段填 "" 或 0，并把字段名写进 uncertain 数组。
2. 明细逐行输出，顺序与单据一致，不要合并、不要遗漏；表头、印章、签名、银行账号、页脚说明不算明细。
3. amount 优先照抄单据金额；单据没有金额时才用 quantity × unit_price 计算。
4. 数量、单价照抄原值（允许小数）；单位按单据原文（件/箱/米/kg/卷等）；规格与品名分列时分别填写。
5. 日期统一 YYYY-MM-DD（两位年份按 20xx）；单号、供应商/客户名称保留原文，不要改写成简称。
6. 这张图只是单据的一页：只输出本页内容，不要把其他页的内容写进来；本页没有明细时 items 输出 []。
7. items 中不要输出合计行、优惠行、运费行；这些信息只出现在 total_amount。"""


@dataclass
class PageResult:
    """单页识别结果（模型原文 + 解析后的 JSON + 图片尺寸）。"""

    index: int
    text: str
    data: dict
    width: int
    height: int
    elapsed_ms: int


def recognize_images(
    db: Session, images: list[tuple[bytes, str]], doc_type: str = "inbound"
) -> dict:
    """识别一张或多张单据照片，返回带候选匹配的草稿。

    images 为 (图片字节, 原始文件名) 列表，最多 settings.VISION_MAX_PAGES 张；
    多页结果按顺序合并，商品行连续编号。
    """
    spec = DOC_SPECS.get(doc_type)
    if not spec:
        raise HTTPException(status_code=400, detail=f"不支持的单据类型：{doc_type}")
    if not settings.vision_enabled:
        raise HTTPException(
            status_code=400,
            detail="未配置视觉模型 Key，请在 .env 填写 LLM_API_KEY 或 VISION_API_KEY 后重启后端",
        )
    if not images:
        raise HTTPException(status_code=400, detail="请至少上传一张单据照片")
    if len(images) > settings.VISION_MAX_PAGES:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多识别 {settings.VISION_MAX_PAGES} 页，请分次识别",
        )

    started = time.time()
    prompt = build_prompt(spec)
    pages: list[PageResult] = []
    for index, (data, filename) in enumerate(images, start=1):
        prepared, mime, width, height = _prepare_image(data)
        page_started = time.time()
        content = _read_page(prepared, mime, prompt, index, len(images))
        parsed = _parse_json(content)
        if not parsed:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"未能从图片中识别出有效单据（第 {index} 页），请换一张更清晰的照片；"
                    "图片已自动压缩，若仍失败可只拍有明细的那一页"
                ),
            )
        pages.append(
            PageResult(
                index=index,
                text=content,
                data=parsed,
                width=width,
                height=height,
                elapsed_ms=int((time.time() - page_started) * 1000),
            )
        )

    catalog = matching.load_catalog(db, spec.product_type)
    product_aliases = matching.alias_index(db, "product")
    partners = matching.load_partners(db, spec.partner_kind)
    partner_aliases = matching.alias_index(db, spec.partner_kind)

    raw_partner = _first_text(pages, spec.partner_field)
    partner_candidates = matching.suggest_partners(
        partners, raw_partner, spec.partner_kind, partner_aliases
    )
    best_partner = matching.pick_best(partner_candidates)
    partner_id = best_partner["id"] if best_partner else None
    partner_name = best_partner["name"] if best_partner else raw_partner

    warnings: list[str] = []
    items: list[dict] = []
    for page in pages:
        page_items = page.data.get("items") or []
        if not isinstance(page_items, list):
            page_items = []
        if not page_items:
            warnings.append(f"第 {page.index} 页没有识别到商品明细")
            continue
        for raw_item in page_items:
            if not isinstance(raw_item, dict):
                continue
            item = _normalize_item(len(items) + 1, raw_item, catalog, product_aliases)
            if item:
                items.append(item)

    document_total = _document_total(pages)
    items_total = round(sum(item["quantity"] * item["unit_price"] for item in items), 2)
    if document_total <= 0:
        document_total = items_total
    diff = round(document_total - items_total, 2)
    total_ok = abs(diff) <= max(0.05, round(items_total * 0.005, 2))
    if items and not total_ok:
        warnings.append(
            f"单据合计 ¥{document_total:.2f} 与明细合计 ¥{items_total:.2f} 相差 {diff:.2f}，请核对"
        )
    unmatched = sum(1 for item in items if not item["product_id"])
    if unmatched:
        warnings.append(f"{unmatched} 行未自动匹配到商品，可选择候选或新建商品")
    uncertain = sorted(
        {
            str(value)
            for page in pages
            for value in (page.data.get("uncertain") or [])
            if isinstance(value, (str, int, float))
        }
    )
    if uncertain:
        warnings.append(f"这些字段字迹不清晰：{'、'.join(uncertain[:8])}")

    date_value = _normalize_date(_first_text(pages, spec.date_field))
    result = {
        "doc_type": spec.key,
        "doc_label": spec.label,
        "partner": {
            "kind": spec.partner_kind,
            "id": partner_id,
            "name": partner_name,
            "raw_name": raw_partner,
            "candidates": partner_candidates,
        },
        "doc_no": _first_text(pages, "doc_no"),
        "date": date_value,
        spec.date_field: date_value,
        "total_amount": round(document_total, 2),
        "total_check": {
            "document_total": round(document_total, 2),
            "items_total": items_total,
            "diff": diff,
            "ok": total_ok,
        },
        "items": items,
        "warnings": warnings,
        "pages": [
            {
                "index": page.index,
                "width": page.width,
                "height": page.height,
                "elapsed_ms": page.elapsed_ms,
            }
            for page in pages
        ],
        "raw_text": _join_text([page.text for page in pages]),
        "ocr_status": "recognized",
        "model": settings.VISION_MODEL,
        "elapsed_ms": int((time.time() - started) * 1000),
    }
    result[spec.partner_field] = partner_name
    if spec.partner_kind == "customer":
        result["customer_id"] = partner_id
        result["customer_name"] = partner_name
        result["contact"] = _first_text(pages, "contact")
        result["address"] = _first_text(pages, "address")
    else:
        result["supplier_id"] = partner_id
        result["supplier_name"] = partner_name
    if spec.key == "purchase":
        result["delivery_date"] = _normalize_date(_first_text(pages, "delivery_date"))
    return result


def recognize_inbound(db: Session, image_bytes: bytes, filename: str) -> dict:
    """兼容旧调用：识别单张入库单照片。"""
    return recognize_images(db, [(image_bytes, filename)], "inbound")


def _first_text(pages: list[PageResult], key: str) -> str:
    for page in pages:
        value = page.data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and value:
            return str(value)
    return ""


def _document_total(pages: list[PageResult]) -> float:
    """合计一般写在最后一页；取最后一个非零值，避免只读到首页小计。"""
    total = 0.0
    for page in pages:
        value = _to_float(page.data.get("total_amount"))
        if value > 0:
            total = value
    return round(total, 2)


def _normalize_item(
    line_no: int,
    raw: dict,
    catalog: list[matching.ProductRow],
    aliases: dict[str, int],
) -> dict | None:
    """把模型返回的一行明细整理成可入库的字段，并给出商品候选。"""
    name = str(raw.get("name") or "").strip()
    sku = str(raw.get("sku") or "").strip()
    spec = str(raw.get("spec") or "").strip()
    unit = str(raw.get("unit") or "").strip()
    quantity = _to_float(raw.get("quantity"))
    price = _to_float(raw.get("unit_price"))
    amount = _to_float(raw.get("amount"))
    if amount > 0 and quantity <= 0 and price > 0:
        quantity = amount / price
    if amount > 0 and price <= 0 and quantity > 0:
        price = round(amount / quantity, 2)
    quantity_int = int(round(quantity)) if quantity > 0 else 0
    if not name and not sku and quantity_int <= 0 and amount <= 0:
        return None
    if quantity_int <= 0:
        quantity_int = 1
    if amount <= 0:
        amount = round(quantity_int * price, 2)

    candidates = matching.suggest_products(
        catalog, name, sku=sku, spec=spec, unit=unit, aliases=aliases
    )
    best = matching.pick_best(candidates)
    if best:
        status = "matched"
        product_id = best["product_id"]
        product_name = best["name"]
        product_sku = best["sku"]
    else:
        status = "suggested" if candidates else "unmatched"
        product_id = None
        product_name = name or sku
        product_sku = sku
    return {
        "line_no": line_no,
        "product_id": product_id,
        "product_name": product_name,
        "product_sku": product_sku,
        "raw_name": name,
        "raw_sku": sku,
        "raw_spec": spec,
        "raw_unit": unit,
        "quantity": quantity_int,
        "unit_price": round(price, 2),
        "amount": round(amount, 2),
        "match_status": status,
        "candidates": candidates,
        "remark": str(raw.get("remark") or "").strip()[:200],
    }


def _prepare_image(data: bytes) -> tuple[bytes, str, int, int]:
    """按 EXIF 转正、限制最长边并重编码为 JPEG，返回 (字节, MIME, 宽, 高)。"""
    if not data:
        raise HTTPException(status_code=400, detail="图片内容为空")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Image.DecompressionBombError as exc:
        raise HTTPException(status_code=400, detail="图片像素过大，请先压缩后再上传") from exc
    except Exception as exc:  # noqa: BLE001 - Pillow 对损坏文件会抛多种异常
        raise HTTPException(
            status_code=400, detail="图片无法读取，请重新拍照或换一张 JPG/PNG 图片"
        ) from exc

    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    if max(image.size) > settings.VISION_MAX_EDGE:
        image.thumbnail((settings.VISION_MAX_EDGE, settings.VISION_MAX_EDGE), Image.LANCZOS)
    # 手机拍摄常偏灰，轻度拉伸对比度能明显改善小字识别
    image = ImageOps.autocontrast(image, cutoff=1)
    buffer = io.BytesIO()
    image.save(
        buffer, format="JPEG", quality=settings.VISION_JPEG_QUALITY, optimize=True
    )
    return buffer.getvalue(), "image/jpeg", image.width, image.height


def _read_page(data: bytes, mime: str, prompt: str, index: int, total: int) -> str:
    """调用视觉模型读取一页；JSON 不合法时再追问一次。"""
    hint = prompt if total == 1 else f"{prompt}\n\n注意：这是第 {index}/{total} 页，只输出本页内容。"
    content = _call_model(data, mime, hint)
    if _parse_json(content):
        return content
    retry_prompt = (
        f"{hint}\n\n上一次输出无法解析为 JSON。请严格只输出一个 JSON 对象，"
        "以 { 开头、以 } 结尾，不要任何其他文字。"
    )
    return _call_model(data, mime, retry_prompt)


def _call_model(data: bytes, mime: str, prompt: str) -> str:
    data_uri = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    payload = {
        "model": settings.VISION_MODEL,
        "temperature": 0.1,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    body = _post_completion(payload)
    choices = body.get("choices") or []
    message = choices[0].get("message", {}) if choices and isinstance(choices[0], dict) else {}
    content = message.get("content", "")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content or "")


def _post_completion(payload: dict) -> dict:
    """带一次重试的模型调用：429/5xx 与连接错误才重试，鉴权错误直接报。"""
    url = f"{settings.VISION_BASE_URL.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.VISION_API_KEY}"}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=settings.VISION_TIMEOUT_SECONDS) as client:
                response = client.post(url, headers=headers, json=payload)
            if response.status_code in RETRYABLE_STATUS and attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail=_status_detail(exc.response.status_code, exc.response.text),
            ) from exc
        except httpx.HTTPError as exc:
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            raise HTTPException(
                status_code=502, detail=f"视觉模型调用失败：连接失败或超时（{exc}）"
            ) from exc
    raise HTTPException(status_code=502, detail="视觉模型调用失败：未知错误")


def _status_detail(status_code: int, text: str) -> str:
    snippet = (text or "").strip()[:200]
    if status_code in (401, 403):
        return f"视觉模型调用失败：鉴权失败（HTTP {status_code}），请检查 VISION_API_KEY"
    if status_code == 404:
        return f"视觉模型调用失败：模型不存在（VISION_MODEL={settings.VISION_MODEL}），请检查配置"
    if status_code == 429:
        return "视觉模型调用失败：调用过于频繁，请稍后重试"
    if status_code == 400 and "image" in snippet.lower():
        return "视觉模型调用失败：模型读不了这张图片，请换 JPG/PNG 或重新拍照"
    return f"视觉模型调用失败（HTTP {status_code}）：{snippet}"


def _join_text(texts: list[str]) -> str:
    parts = []
    for index, text in enumerate(texts, start=1):
        body = str(text or "").strip()
        if body:
            parts.append(body if len(texts) == 1 else f"--- 第 {index} 页 ---\n{body}")
    return "\n\n".join(parts)[:4000]


def _parse_json(text: str) -> dict | None:
    """从模型输出里取出 JSON：容忍代码块、前后废话、中文引号与尾逗号。"""
    cleaned = str(text or "").strip()
    if not cleaned:
        return None
    cleaned = re.sub(r"```(?:json)?", "", cleaned).strip("` \n")
    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        candidates.insert(0, cleaned[start : end + 1])
    for candidate in candidates:
        for variant in (candidate, _repair_json(candidate)):
            try:
                data = json.loads(variant)
            except (json.JSONDecodeError, TypeError):
                continue
            return data if isinstance(data, dict) else None
    return None


def _repair_json(text: str) -> str:
    """修复模型常见的 JSON 小毛病，减少一次重试开销。"""
    repaired = (
        text.replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
        .replace("：", ":")
        .replace("\u00a0", " ")
    )
    # 去掉对象/数组末尾多余的逗号
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    # 单引号包裹的键或值转双引号（内容里没有双引号时才安全）
    if '"' not in repaired:
        repaired = repaired.replace("'", '"')
    return repaired


def _to_int(value) -> int:
    return int(_to_float(value))


def _to_float(value) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("，", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return 0.0
    try:
        return float(match.group())
    except ValueError:
        return 0.0


def _normalize_date(value: str) -> str:
    """把 2026年3月5日 / 2026.3.5 / 26-3-5 统一成 YYYY-MM-DD；无法识别返回空串。"""
    if not value:
        return ""
    match = re.search(r"(\d{2,4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})", str(value))
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups())
    if year < 100:
        year += 2000
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


# ---- 旧接口兼容：测试与历史调用使用这两个函数 ----

def _match_product(db: Session, sku: str, name: str) -> Product | None:
    """按 SKU/名称匹配商品，返回最相似的一个（用于旧调用）。"""
    catalog = matching.load_catalog(db)
    candidates = matching.suggest_products(
        catalog, name, sku=sku, aliases=matching.alias_index(db, "product")
    )
    if not candidates:
        return None
    return db.get(Product, candidates[0]["product_id"])


def _match_supplier(db: Session, name: str) -> Supplier | None:
    """按名称匹配供应商，返回最相似的一个（用于旧调用）。"""
    rows = matching.load_partners(db, "supplier")
    candidates = matching.suggest_partners(
        rows, name, "supplier", matching.alias_index(db, "supplier")
    )
    if not candidates:
        return None
    return db.get(Supplier, candidates[0]["id"])


def _match_customer(db: Session, name: str) -> Customer | None:
    """按名称匹配客户，返回最相似的一个（用于旧调用）。"""
    rows = matching.load_partners(db, "customer")
    candidates = matching.suggest_partners(
        rows, name, "customer", matching.alias_index(db, "customer")
    )
    if not candidates:
        return None
    return db.get(Customer, candidates[0]["id"])
