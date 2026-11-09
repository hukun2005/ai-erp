import math
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import require_perm
from ..models import (
    BillOfMaterials,
    Product,
    ProductionExpense,
    ProductionMaterial,
    ProductionOrder,
    ProductionProcess,
    User,
    Warehouse,
)
from ..schemas import (
    MaterialIssue,
    ProcessScan,
    ProductionAction,
    ProductionCreate,
    ProductionExpenseIn,
)
from ..services.product_type import ensure_product_type
from ..services.stock import apply_stock_change, default_warehouse
from ..utils import gen_order_no

router = APIRouter(prefix="/api/production-orders", tags=["生产订单"])

DEFAULT_PROCESSES = ["备料", "加工", "装配", "检验", "包装"]


def serialize_order(order: ProductionOrder) -> dict:
    material_cost = sum(
        (item.issued_qty or 0) * (item.product.cost_price if item.product else 0.0)
        for item in order.materials
    )
    expense_cost = sum(expense.amount for expense in order.expenses)
    total_processes = len(order.processes)
    done_processes = len([p for p in order.processes if p.status == "completed"])
    return {
        "id": order.id,
        "order_no": order.order_no,
        "product_id": order.product_id,
        "product_name": order.product.name if order.product else "",
        "product_sku": order.product.sku if order.product else "",
        "product_type": order.product.product_type if order.product else "",
        "unit": order.product.unit if order.product else "",
        "bom_id": order.bom_id,
        "bom_no": order.bom.bom_no if order.bom else "",
        "quantity": order.quantity,
        "warehouse_id": order.warehouse_id,
        "warehouse_name": order.warehouse.name if order.warehouse else "",
        "status": order.status,
        "plan_start": order.plan_start,
        "plan_end": order.plan_end,
        "source_order_id": order.source_order_id,
        "remark": order.remark,
        "created_by": order.created_by,
        "created_at": order.created_at,
        "released_at": order.released_at,
        "completed_at": order.completed_at,
        "material_cost": round(material_cost, 2),
        "expense_cost": round(expense_cost, 2),
        "total_cost": round(material_cost + expense_cost, 2),
        "process_total": total_processes,
        "process_done": done_processes,
        "progress": round(done_processes / total_processes * 100) if total_processes else 0,
        "materials": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product.name if item.product else "",
                "product_sku": item.product.sku if item.product else "",
                "product_type": item.product.product_type if item.product else "",
                "unit": item.product.unit if item.product else "",
                "required_qty": item.required_qty,
                "issued_qty": item.issued_qty,
                "remaining_qty": max(round(item.required_qty - item.issued_qty, 4), 0),
                "stock_qty": item.product.stock_qty if item.product else 0,
                "cost_price": item.product.cost_price if item.product else 0.0,
                "warehouse_id": item.warehouse_id,
            }
            for item in order.materials
        ],
        "processes": [
            {
                "id": step.id,
                "seq": step.seq,
                "name": step.name,
                "status": step.status,
                "operator": step.operator,
                "scanned_at": step.scanned_at,
                "remark": step.remark,
            }
            for step in order.processes
        ],
        "expenses": [
            {
                "id": expense.id,
                "category": expense.category,
                "amount": expense.amount,
                "remark": expense.remark,
                "created_by": expense.created_by,
                "created_at": expense.created_at,
            }
            for expense in order.expenses
        ],
    }


def _get(db: Session, order_id: int) -> ProductionOrder:
    order = db.get(ProductionOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="生产订单不存在")
    return order


def _active_bom(db: Session, product_id: int) -> BillOfMaterials | None:
    return db.scalar(
        select(BillOfMaterials)
        .where(BillOfMaterials.product_id == product_id)
        .where(BillOfMaterials.status == "active")
        .order_by(BillOfMaterials.id.desc())
    )


def _refresh_cost(order: ProductionOrder) -> None:
    material_cost = sum(
        (item.issued_qty or 0) * (item.product.cost_price if item.product else 0.0)
        for item in order.materials
    )
    expense_cost = sum(expense.amount for expense in order.expenses)
    order.total_cost = round(material_cost + expense_cost, 2)


@router.get("", summary="生产订单列表")
def list_orders(
    keyword: str = Query(""),
    status: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("production:view")),
):
    stmt = select(ProductionOrder).join(Product, Product.id == ProductionOrder.product_id)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(ProductionOrder.order_no.like(like), Product.name.like(like)))
    if status:
        stmt = stmt.where(ProductionOrder.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(ProductionOrder.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"total": total, "items": [serialize_order(row) for row in rows]}


@router.get("/{order_id}", summary="生产订单详情")
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_perm("production:view")),
):
    return serialize_order(_get(db, order_id))


@router.post("", summary="新建生产订单（草稿）", dependencies=[Depends(require_perm("production:manage"))])
def create_order(
    payload: ProductionCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("production:manage")),
):
    product = db.get(Product, payload.product_id)
    if not product:
        raise HTTPException(status_code=400, detail="成品不存在")
    ensure_product_type(product, "finished_good", "生产订单产出")
    if not db.get(Warehouse, payload.warehouse_id):
        raise HTTPException(status_code=400, detail="入库仓库不存在")

    bom = db.get(BillOfMaterials, payload.bom_id) if payload.bom_id else _active_bom(db, payload.product_id)
    if payload.bom_id and not bom:
        raise HTTPException(status_code=400, detail="BOM 不存在")

    order = ProductionOrder(
        order_no=gen_order_no(db, ProductionOrder, "MO"),
        product_id=payload.product_id,
        bom_id=bom.id if bom else None,
        quantity=payload.quantity,
        warehouse_id=payload.warehouse_id,
        status="draft",
        plan_start=payload.plan_start,
        plan_end=payload.plan_end,
        remark=payload.remark,
        created_by=user.username,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    record(
        username=user.username,
        role=user.role,
        action="新建生产订单",
        resource=f"生产订单 {order.order_no}",
        detail=f"成品 {product.name} × {order.quantity}",
        request=request,
    )
    return serialize_order(order)


@router.post("/{order_id}/status", summary="下达 / 完工入库 / 取消生产")
def change_status(
    order_id: int,
    payload: ProductionAction,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("production:manage")),
):
    order = _get(db, order_id)
    action = payload.action

    if action == "release":
        if order.status != "draft":
            raise HTTPException(status_code=400, detail="只有草稿生产订单可以下达")
        bom = db.get(BillOfMaterials, order.bom_id) if order.bom_id else _active_bom(db, order.product_id)
        if not bom:
            raise HTTPException(status_code=400, detail="该成品没有启用的 BOM，请先在 BOM 清单中配置用料")
        ensure_product_type(order.product, "finished_good", "生产产出")
        ensure_product_type(bom.product, "finished_good", "BOM 成品")
        for item in bom.items:
            ensure_product_type(item.product, "raw_material", "生产领料")
        order.bom_id = bom.id
        order.materials.clear()
        for item in bom.items:
            required = math.ceil(item.quantity * order.quantity / (bom.base_qty or 1))
            order.materials.append(
                ProductionMaterial(product_id=item.product_id, required_qty=required, issued_qty=0)
            )
        order.processes.clear()
        for index, name in enumerate(DEFAULT_PROCESSES, start=1):
            order.processes.append(ProductionProcess(seq=index, name=name, status="pending"))
        order.status = "released"
        order.released_at = datetime.now()
        _refresh_cost(order)

    elif action == "complete":
        if order.status not in ("released", "in_progress"):
            raise HTTPException(status_code=400, detail="只有已下达的生产订单可以完工")
        shortages = [
            f"{item.product.name}（需 {item.required_qty:g}，已领 {item.issued_qty:g}）"
            for item in order.materials
            if item.issued_qty < item.required_qty
        ]
        if shortages:
            raise HTTPException(status_code=400, detail="用料尚未领齐：" + "；".join(shortages))
        ensure_product_type(order.product, "finished_good", "生产完工入库")
        # 产品入库：按生产订单数量入成品仓
        apply_stock_change(
            db,
            order.product,
            "in",
            order.quantity,
            warehouse_id=order.warehouse_id,
            reason=f"生产订单 {order.order_no} 完工入库",
            biz_type="生产入库",
            ref_no=order.order_no,
            username=user.username,
        )
        _refresh_cost(order)
        order.status = "completed"
        order.completed_at = datetime.now()
        # 用实际生产成本回写商品单位成本，后续销售毛利按此计算
        if order.quantity > 0 and order.total_cost > 0:
            order.product.cost_price = round(order.total_cost / order.quantity, 2)

    elif action == "cancel":
        if order.status == "completed":
            raise HTTPException(status_code=400, detail="已完工的生产订单不能取消")
        if order.status == "cancelled":
            raise HTTPException(status_code=400, detail="生产订单已取消")
        # 已领用料退回原领料仓
        for item in order.materials:
            if item.issued_qty > 0:
                apply_stock_change(
                    db,
                    item.product,
                    "in",
                    int(item.issued_qty),
                    warehouse_id=item.warehouse_id,
                    reason=f"生产订单 {order.order_no} 取消退料",
                    biz_type="生产退料",
                    ref_no=order.order_no,
                    username=user.username,
                )
                item.issued_qty = 0
        order.status = "cancelled"
        order.cancelled_at = datetime.now()

    else:
        raise HTTPException(status_code=400, detail=f"不支持的操作：{action}")

    db.commit()
    db.refresh(order)
    labels = {"release": "下达生产", "complete": "完工入库", "cancel": "取消生产"}
    record(
        username=user.username,
        role=user.role,
        action="生产订单状态变更",
        resource=f"生产订单 {order.order_no}",
        detail=labels.get(action, action),
        request=request,
    )
    return serialize_order(order)


@router.post("/{order_id}/issue", summary="生产领料（按 BOM 扣减库存）")
def issue_materials(
    order_id: int,
    payload: MaterialIssue,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("production:manage")),
):
    order = _get(db, order_id)
    if order.status not in ("released", "in_progress"):
        raise HTTPException(status_code=400, detail="只有已下达或生产中的订单可以领料")

    warehouse = (
        db.get(Warehouse, payload.warehouse_id) if payload.warehouse_id else default_warehouse(db)
    )
    if not warehouse:
        raise HTTPException(status_code=400, detail="领料仓库不存在")

    targets = {item.product_id: item for item in order.materials}
    requests: list[tuple[ProductionMaterial, int]] = []
    if payload.items:
        for entry in payload.items:
            material = targets.get(entry.product_id)
            if not material:
                raise HTTPException(status_code=400, detail=f"物料 {entry.product_id} 不在本单用料清单中")
            qty = int(entry.quantity)
            if qty <= 0:
                raise HTTPException(status_code=400, detail="领料数量必须大于 0")
            remaining = material.required_qty - material.issued_qty
            if qty > remaining:
                raise HTTPException(
                    status_code=400,
                    detail=f"{material.product.name} 剩余待领 {remaining:g}，本次领料 {qty} 超出",
                )
            requests.append((material, qty))
    else:
        for material in order.materials:
            remaining = material.required_qty - material.issued_qty
            if remaining > 0:
                requests.append((material, int(remaining)))

    if not requests:
        raise HTTPException(status_code=400, detail="本单用料已全部领齐，无需重复领料")

    for material, qty in requests:
        ensure_product_type(material.product, "raw_material", "生产领料")
        apply_stock_change(
            db,
            material.product,
            "out",
            qty,
            warehouse_id=warehouse.id,
            reason=f"生产订单 {order.order_no} 领料",
            biz_type="生产领料",
            ref_no=order.order_no,
            username=user.username,
        )
        material.issued_qty = round(material.issued_qty + qty, 4)
        material.warehouse_id = warehouse.id

    if order.status == "released":
        order.status = "in_progress"
    _refresh_cost(order)
    db.commit()
    db.refresh(order)
    record(
        username=user.username,
        role=user.role,
        action="生产领料",
        resource=f"生产订单 {order.order_no}",
        detail=f"仓库 {warehouse.name}，领料 {len(requests)} 行",
        request=request,
    )
    return serialize_order(order)


@router.post("/{order_id}/processes/{process_id}/scan", summary="工序扫码报工")
def scan_process(
    order_id: int,
    process_id: int,
    payload: ProcessScan,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("production:manage")),
):
    order = _get(db, order_id)
    step = db.get(ProductionProcess, process_id)
    if not step or step.production_id != order.id:
        raise HTTPException(status_code=404, detail="工序不存在")
    if step.status == "completed":
        raise HTTPException(status_code=400, detail="该工序已完成，无需重复扫码")

    step.status = "completed"
    step.operator = payload.operator or user.username
    step.scanned_at = datetime.now()
    step.remark = payload.remark
    if order.status == "released":
        order.status = "in_progress"
    db.commit()
    db.refresh(order)
    record(
        username=user.username,
        role=user.role,
        action="工序扫码",
        resource=f"生产订单 {order.order_no}",
        detail=f"{step.name} · {step.operator}",
        request=request,
    )
    return serialize_order(order)


@router.post("/{order_id}/expenses", summary="登记生产费用")
def add_expense(
    order_id: int,
    payload: ProductionExpenseIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("production:manage")),
):
    order = _get(db, order_id)
    if order.status in ("completed", "cancelled"):
        raise HTTPException(status_code=400, detail="已完工或已取消的订单不能登记费用")
    db.add(
        ProductionExpense(
            production_id=order.id,
            category=payload.category,
            amount=payload.amount,
            remark=payload.remark,
            created_by=user.username,
        )
    )
    db.flush()
    db.refresh(order)
    _refresh_cost(order)
    db.commit()
    db.refresh(order)
    return serialize_order(order)


@router.delete("/{order_id}/expenses/{expense_id}", summary="删除生产费用")
def delete_expense(
    order_id: int,
    expense_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("production:manage")),
):
    order = _get(db, order_id)
    expense = db.get(ProductionExpense, expense_id)
    if not expense or expense.production_id != order.id:
        raise HTTPException(status_code=404, detail="费用记录不存在")
    if order.status in ("completed", "cancelled"):
        raise HTTPException(status_code=400, detail="已完工或已取消的订单不能修改费用")
    db.delete(expense)
    db.flush()
    db.refresh(order)
    _refresh_cost(order)
    db.commit()
    db.refresh(order)
    return serialize_order(order)


@router.delete("/{order_id}", summary="删除草稿生产订单")
def delete_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("production:manage")),
):
    order = _get(db, order_id)
    if order.status != "draft":
        raise HTTPException(status_code=400, detail="只有草稿生产订单可以删除")
    db.delete(order)
    db.commit()
    return {"ok": True}
