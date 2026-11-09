from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import require_perm
from ..models import Customer, SalesOrder, Supplier, PurchaseOrder, User
from ..services.importer import XLSX_MEDIA_TYPE, build_template, parse_number, parse_workbook
from ..scoping import ensure_owner_or_public, own_scope
from ..schemas import (
    CustomerCreate,
    CustomerOut,
    CustomerUpdate,
    SupplierCreate,
    SupplierOut,
    SupplierUpdate,
)

router = APIRouter()

CUSTOMER_COLUMNS = ["客户名称", "联系人", "电话", "邮箱", "地址", "等级", "信用额度", "备注"]
CUSTOMER_SAMPLE = [["示例客户", "张三", "13800000000", "demo@example.com", "上海市", "B", 50000, ""]]


# ---------------- 客户 ----------------
@router.get("/api/customers", summary="客户列表", dependencies=[Depends(require_perm("customer:view"))])
def list_customers(
    keyword: str = Query(""),
    level: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("customer:view")),
):
    stmt = select(Customer)
    if own_scope(user.role):
        # 仅本人负责的客户 + 公海客户
        stmt = stmt.where(or_(Customer.owner == user.username, Customer.owner == ""))
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(Customer.name.like(like), Customer.contact.like(like), Customer.phone.like(like)))
    if level:
        stmt = stmt.where(Customer.level == level)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Customer.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"total": total, "items": [CustomerOut.model_validate(c) for c in rows]}


@router.get("/api/customers/all", summary="全量客户（下拉用）", dependencies=[Depends(require_perm("customer:view"))])
def all_customers(db: Session = Depends(get_db), user: User = Depends(require_perm("customer:view"))):
    stmt = select(Customer).order_by(Customer.name)
    if own_scope(user.role):
        stmt = select(Customer).where(or_(Customer.owner == user.username, Customer.owner == "")).order_by(Customer.name)
    rows = db.scalars(stmt).all()
    return [{"id": c.id, "name": c.name, "level": c.level} for c in rows]


@router.post("/api/customers", response_model=CustomerOut, summary="新建客户", dependencies=[Depends(require_perm("customer:manage"))])
def create_customer(
    payload: CustomerCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("customer:manage")),
):
    customer = Customer(**payload.model_dump())
    # 销售员新建客户自动归为本人负责
    customer.owner = user.username if own_scope(user.role) else ""
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return CustomerOut.model_validate(customer)


@router.put("/api/customers/{customer_id}", response_model=CustomerOut, summary="更新客户", dependencies=[Depends(require_perm("customer:manage"))])
def update_customer(
    customer_id: int,
    payload: CustomerUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("customer:manage")),
):
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="客户不存在")
    ensure_owner_or_public(customer.owner, user, "编辑")
    if own_scope(user.role) and not customer.owner:
        customer.owner = user.username  # 编辑公海客户即视为领取
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(customer, key, value)
    db.commit()
    db.refresh(customer)
    return CustomerOut.model_validate(customer)


@router.delete("/api/customers/{customer_id}", summary="删除客户", dependencies=[Depends(require_perm("customer:manage"))])
def delete_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("customer:manage")),
):
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="客户不存在")
    ensure_owner_or_public(customer.owner, user, "删除")
    if db.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.customer_id == customer_id)):
        raise HTTPException(status_code=400, detail="该客户已有销售订单，无法删除")
    db.delete(customer)
    db.commit()
    return {"ok": True}


# ---------------- 供应商 ----------------
@router.get("/api/suppliers", summary="供应商列表", dependencies=[Depends(require_perm("supplier:view"))])
def list_suppliers(
    keyword: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = select(Supplier)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(Supplier.name.like(like), Supplier.contact.like(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Supplier.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"total": total, "items": [SupplierOut.model_validate(s) for s in rows]}


@router.get("/api/suppliers/all", summary="全量供应商（下拉用）", dependencies=[Depends(require_perm("supplier:view"))])
def all_suppliers(db: Session = Depends(get_db)):
    rows = db.scalars(select(Supplier).order_by(Supplier.name)).all()
    return [{"id": s.id, "name": s.name, "supply_category": s.supply_category} for s in rows]


@router.post("/api/suppliers", response_model=SupplierOut, summary="新建供应商", dependencies=[Depends(require_perm("supplier:manage"))])
def create_supplier(payload: SupplierCreate, db: Session = Depends(get_db)):
    supplier = Supplier(**payload.model_dump())
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return SupplierOut.model_validate(supplier)


@router.put("/api/suppliers/{supplier_id}", response_model=SupplierOut, summary="更新供应商", dependencies=[Depends(require_perm("supplier:manage"))])
def update_supplier(supplier_id: int, payload: SupplierUpdate, db: Session = Depends(get_db)):
    supplier = db.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="供应商不存在")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(supplier, key, value)
    db.commit()
    db.refresh(supplier)
    return SupplierOut.model_validate(supplier)


@router.delete("/api/suppliers/{supplier_id}", summary="删除供应商", dependencies=[Depends(require_perm("supplier:manage"))])
def delete_supplier(supplier_id: int, db: Session = Depends(get_db)):
    supplier = db.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="供应商不存在")
    if db.scalar(select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.supplier_id == supplier_id)):
        raise HTTPException(status_code=400, detail="该供应商已有采购订单，无法删除")
    db.delete(supplier)
    db.commit()
    return {"ok": True}


# ---------------- 客户批量导入 ----------------
@router.get(
    "/api/customers/import-template",
    summary="下载客户导入模板",
    dependencies=[Depends(require_perm("customer:view"))],
)
def download_customer_template():
    content = build_template(CUSTOMER_COLUMNS, CUSTOMER_SAMPLE, "客户导入")
    filename = quote("客户导入模板.xlsx")
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.post(
    "/api/customers/import",
    summary="批量导入客户（Excel）",
    dependencies=[Depends(require_perm("customer:manage"))],
)
def import_customers(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("customer:manage")),
):
    """按客户名称去重；销售员导入的客户自动归为本人负责。"""
    try:
        rows = parse_workbook(file.file.read(), CUSTOMER_COLUMNS)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing = set(db.scalars(select(Customer.name)).all())
    owner = user.username if own_scope(user.role) else ""
    created = 0
    skipped = 0
    errors: list[dict] = []
    seen: set[str] = set()

    for row in rows:
        line = row["_row"]
        name = str(row.get("客户名称") or "").strip()
        if not name:
            errors.append({"row": line, "reason": "客户名称必填"})
            continue
        if name in existing or name in seen:
            skipped += 1
            errors.append({"row": line, "reason": f"客户「{name}」已存在，已跳过"})
            continue
        level = str(row.get("等级") or "C").strip().upper() or "C"
        if level not in {"A", "B", "C"}:
            errors.append({"row": line, "reason": f"等级只能是 A/B/C，当前为「{level}」"})
            continue
        try:
            credit_limit = parse_number(row.get("信用额度"))
        except ValueError as exc:
            errors.append({"row": line, "reason": str(exc)})
            continue

        db.add(
            Customer(
                name=name,
                contact=str(row.get("联系人") or "").strip(),
                phone=str(row.get("电话") or "").strip(),
                email=str(row.get("邮箱") or "").strip(),
                address=str(row.get("地址") or "").strip(),
                level=level,
                credit_limit=credit_limit,
                remark=str(row.get("备注") or "").strip(),
                owner=owner,
            )
        )
        seen.add(name)
        created += 1

    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="导入客户",
        resource="客户主数据",
        detail=f"新增 {created} 条，跳过 {skipped} 条，失败 {len(errors) - skipped} 条",
        request=request,
    )
    return {
        "created": created,
        "skipped": skipped,
        "errors": errors[:50],
        "error_count": len(errors),
    }
