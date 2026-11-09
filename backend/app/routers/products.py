from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import get_current_user, require_perm
from ..models import Product, PurchaseOrderItem, SalesOrderItem, User
from ..permissions import can_view_cost
from ..schemas import ProductCreate, ProductOut, ProductQuickCreate, ProductUpdate
from ..services.importer import (
    XLSX_MEDIA_TYPE,
    build_template,
    parse_int,
    parse_number,
    parse_product_type,
    parse_workbook,
)
from ..services.stock import apply_stock_change, default_location, default_warehouse

router = APIRouter(prefix="/api/products", tags=["商品"])

# 导入列顺序即模板列顺序；期初库存会走库存服务写台账，不会直接改商品总库存
PRODUCT_COLUMNS = ["SKU", "名称", "分类", "物料类型", "单位", "规格", "成本价", "售价", "安全库存", "期初库存"]
PRODUCT_SAMPLE = [
    ["SKU0001", "示例商品", "默认分类", "成品", "件", "规格 A", 10.5, 19.9, 20, 100]
]


@router.get("", summary="商品列表（支持搜索/分类/库存预警）", dependencies=[Depends(require_perm("product:view"))])
def list_products(
    keyword: str = Query("", description="SKU 或名称模糊搜索"),
    category: str = Query("", description="分类过滤"),
    product_type: str = Query("", description="物料类型：raw_material / finished_good"),
    only_low_stock: bool = Query(False, description="只看库存预警商品"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(Product)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            or_(Product.sku.like(like), Product.name.like(like), Product.barcode.like(like))
        )
    if category:
        stmt = stmt.where(Product.category == category)
    product_type = (product_type or "").strip()
    if product_type:
        if product_type not in ("raw_material", "finished_good"):
            raise HTTPException(
                status_code=400, detail="物料类型不合法，可选：raw_material / finished_good"
            )
        stmt = stmt.where(Product.product_type == product_type)
    if only_low_stock:
        stmt = stmt.where(Product.stock_qty <= Product.safety_stock)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Product.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = [ProductOut.model_validate(p) for p in rows]
    if not can_view_cost(user.role):
        for it in items:
            it.cost_price = 0.0  # 字段级权限：无成本查看权时脱敏
    return {"total": total, "items": items}


@router.get("/categories", summary="商品分类列表", dependencies=[Depends(require_perm("product:view"))])
def list_categories(db: Session = Depends(get_db)):
    return [c for c in db.scalars(select(Product.category).distinct()).all() if c]


@router.get("/scan", summary="扫码查询商品（条码/SKU）", dependencies=[Depends(require_perm("product:view"))])
def scan_product(
    code: str = Query(..., min_length=1, description="扫码得到的条码，兼容直接输入 SKU"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """扫码录单：先按条码精确查，查不到再按 SKU 兜底，都没有返回 404。"""
    text = code.strip()
    product = db.scalar(select(Product).where(Product.barcode == text, Product.barcode != ""))
    if product is None:
        product = db.scalar(select(Product).where(Product.sku == text))
    if product is None:
        raise HTTPException(status_code=404, detail=f"未找到条码或 SKU 为「{text}」的商品")
    data = ProductOut.model_validate(product)
    if not can_view_cost(user.role):
        data.cost_price = 0.0
    return data


def _ensure_barcode_free(db: Session, barcode: str, exclude_id: int | None = None) -> None:
    """条码用于扫码录单，重复会导致扫出两个商品，建档/改档时挡住。"""
    text = (barcode or "").strip()
    if not text:
        return
    stmt = select(Product.id).where(Product.barcode == text)
    if exclude_id is not None:
        stmt = stmt.where(Product.id != exclude_id)
    if db.scalar(stmt.limit(1)):
        raise HTTPException(status_code=400, detail=f"条码 {text} 已被其他商品占用")


@router.post("", response_model=ProductOut, summary="新建商品", dependencies=[Depends(require_perm("product:manage"))])
def create_product(payload: ProductCreate, db: Session = Depends(get_db)):
    if db.scalar(select(Product).where(Product.sku == payload.sku)):
        raise HTTPException(status_code=400, detail="SKU 已存在")
    _ensure_barcode_free(db, payload.barcode)
    data = payload.model_dump()
    data["barcode"] = (data.get("barcode") or "").strip()
    product = Product(**data)
    db.add(product)
    db.commit()
    db.refresh(product)
    return ProductOut.model_validate(product)


@router.post(
    "/quick",
    response_model=ProductOut,
    summary="识别时快速新建商品（SKU 自动生成）",
    dependencies=[Depends(require_perm("product:manage"))],
)
def quick_create_product(payload: ProductQuickCreate, db: Session = Depends(get_db)):
    """单据识别到系统里没有的商品时用：只填名称即可建档。

    SKU 按「P + 日期 + 序号」生成，避免手填与既有编码冲突；同一个识别名再次出现
    会由别名表直接命中，不会重复建档。
    """
    head = f"P{datetime.now().strftime('%Y%m%d')}"
    existing = db.scalars(select(Product.sku).where(Product.sku.like(f"{head}%"))).all()
    sequence = 0
    for sku in existing:
        suffix = str(sku)[len(head):]
        if suffix.isdigit():
            sequence = max(sequence, int(suffix))
    product = Product(
        sku=f"{head}{sequence + 1:03d}",
        name=payload.name.strip(),
        category=payload.category or "默认分类",
        product_type=payload.product_type,
        unit=payload.unit or "件",
        spec=payload.spec or "",
        cost_price=max(payload.cost_price, 0),
        sale_price=max(payload.sale_price, 0),
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return ProductOut.model_validate(product)


@router.put("/{product_id}", response_model=ProductOut, summary="更新商品", dependencies=[Depends(require_perm("product:manage"))])
def update_product(product_id: int, payload: ProductUpdate, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    data = payload.model_dump(exclude_unset=True)
    if "barcode" in data:
        data["barcode"] = (data["barcode"] or "").strip()
        _ensure_barcode_free(db, data["barcode"], exclude_id=product_id)
    for key, value in data.items():
        setattr(product, key, value)
    db.commit()
    db.refresh(product)
    return ProductOut.model_validate(product)


@router.delete("/{product_id}", summary="删除商品", dependencies=[Depends(require_perm("product:manage"))])
def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    has_sales = db.scalar(
        select(func.count()).select_from(SalesOrderItem).where(SalesOrderItem.product_id == product_id)
    )
    has_purchase = db.scalar(
        select(func.count()).select_from(PurchaseOrderItem).where(PurchaseOrderItem.product_id == product_id)
    )
    if has_sales or has_purchase:
        raise HTTPException(status_code=400, detail="该商品已被订单引用，建议改为下架（is_active=false）")
    db.delete(product)
    db.commit()
    return {"ok": True}


@router.get("/import-template", summary="下载商品导入模板", dependencies=[Depends(require_perm("product:view"))])
def download_import_template():
    content = build_template(PRODUCT_COLUMNS, PRODUCT_SAMPLE, "商品导入")
    filename = quote("商品导入模板.xlsx")
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.post("/import", summary="批量导入商品（Excel）", dependencies=[Depends(require_perm("product:manage"))])
def import_products(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("product:manage")),
):
    """逐行导入商品；期初库存经库存服务写台账，保证库位余额与总库存一致。"""
    try:
        rows = parse_workbook(file.file.read(), PRODUCT_COLUMNS)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def opening_of(row: dict) -> int:
        """预判该行是否要入期初库存；非法值留给主循环逐行报错。"""
        try:
            return parse_int(row.get("期初库存"))
        except ValueError:
            return 0

    if any(opening_of(row) > 0 for row in rows):
        try:
            default_location(db, default_warehouse(db))
        except HTTPException as exc:
            raise HTTPException(
                status_code=400,
                detail="导入期初库存前，请先在「仓库与库位」中新建可用的仓库和库位",
            ) from exc

    existing = set(db.scalars(select(Product.sku)).all())
    created = 0
    skipped = 0
    errors: list[dict] = []
    seen: set[str] = set()

    for row in rows:
        line = row["_row"]
        sku = str(row.get("SKU") or "").strip()
        name = str(row.get("名称") or "").strip()
        if not sku or not name:
            errors.append({"row": line, "reason": "SKU 与名称必填"})
            continue
        if sku in existing or sku in seen:
            skipped += 1
            errors.append({"row": line, "reason": f"SKU {sku} 已存在，已跳过"})
            continue
        try:
            product = Product(
                sku=sku,
                name=name,
                category=str(row.get("分类") or "默认分类").strip() or "默认分类",
                product_type=parse_product_type(row.get("物料类型")),
                unit=str(row.get("单位") or "件").strip() or "件",
                spec=str(row.get("规格") or "").strip(),
                cost_price=parse_number(row.get("成本价")),
                sale_price=parse_number(row.get("售价")),
                safety_stock=parse_int(row.get("安全库存")),
                stock_qty=0,
            )
            opening = parse_int(row.get("期初库存"))
        except ValueError as exc:
            errors.append({"row": line, "reason": str(exc)})
            continue

        db.add(product)
        seen.add(sku)
        created += 1
        if opening > 0:
            db.flush()  # 期初库存需要商品 id
            apply_stock_change(
                db,
                product,
                "in",
                opening,
                reason="期初库存导入",
                biz_type="期初导入",
                username=user.username,
            )

    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="导入商品",
        resource="商品主数据",
        detail=f"新增 {created} 条，跳过 {skipped} 条，失败 {len(errors) - skipped} 条",
        request=request,
    )
    return {
        "created": created,
        "skipped": skipped,
        "errors": errors[:50],
        "error_count": len(errors),
    }
