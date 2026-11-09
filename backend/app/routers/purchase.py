from datetime import datetime
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import list_filters
from ..audit import record
from ..database import get_db
from ..deps import get_current_user, require_perm
from ..models import Product, PurchaseOrder, PurchaseOrderItem, Supplier, User
from ..permissions import has_permission
from ..schemas import PurchaseOrderCreate, PurchaseOrderUpdate, StatusUpdate
from ..scoping import apply_owner_scope, ensure_owner
from ..services import alerts, approvals
from ..services import approval_flow, matching
from ..services.docx_render import build_docx_download, build_purchase_document, money
from ..services.product_type import ensure_product_type
from ..services.stock import apply_stock_change, deduct_stock
from ..utils import gen_order_no, serialize_purchase_order

router = APIRouter(prefix="/api/purchase-orders", tags=["采购订单"])

STATUS_TEXT = {
    "draft": "草稿",
    "pending": "待审批",
    "ordered": "已下单",
    "received": "已入库",
    "cancelled": "已取消",
}


def _get_order(db: Session, order_id: int, user: User, action: str = "查看") -> PurchaseOrder:
    order = db.get(PurchaseOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="采购单不存在")
    ensure_owner(order.created_by, user, action)
    return order


def _learn_aliases(db: Session, items, raw_supplier_name: str, supplier: Supplier) -> None:
    """把用户确认过的「识别名 → 商品/供应商」记下来，下次识别直接命中。"""
    entries = []
    for item in items or []:
        product_id = item.get("product_id") if isinstance(item, dict) else item.product_id
        raw_name = item.get("raw_name") if isinstance(item, dict) else item.raw_name
        if product_id and raw_name:
            entries.append((product_id, raw_name))
    if entries:
        names = matching.product_name_map(db, [product_id for product_id, _ in entries])
        matching.learn_item_aliases(db, entries, names, source="ocr")
    if raw_supplier_name:
        matching.learn_alias(
            db,
            "supplier",
            supplier.id,
            raw_supplier_name,
            canonical=supplier.name,
            source="ocr",
        )


@router.get("", summary="采购订单列表")
def list_orders(
    keyword: str = Query(""),
    status: str = Query(""),
    supplier_id: int | None = Query(None),
    created_by: str = Query(""),
    start_date: str = Query("", description="下单日期起（YYYY-MM-DD，含当天）"),
    end_date: str = Query("", description="下单日期止（YYYY-MM-DD，含当天）"),
    amount_min: float | None = Query(None, description="采购金额下限"),
    amount_max: float | None = Query(None, description="采购金额上限"),
    sort: str = Query("", description="排序：created_desc/date_desc/date_asc/amount_desc/amount_asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("purchase:view")),
):
    stmt = select(PurchaseOrder).join(Supplier)
    stmt = apply_owner_scope(stmt, PurchaseOrder.created_by, user)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(PurchaseOrder.order_no.like(like), Supplier.name.like(like)))
    if status:
        stmt = stmt.where(PurchaseOrder.status == status)
    if supplier_id:
        stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
    stmt = list_filters.apply_common_filters(
        stmt,
        PurchaseOrder,
        start_date=start_date,
        end_date=end_date,
        amount_min=amount_min,
        amount_max=amount_max,
        created_by=created_by,
    )
    subquery = stmt.subquery()
    total = db.scalar(select(func.count()).select_from(subquery)) or 0
    amount_sum = db.scalar(
        select(func.coalesce(func.sum(subquery.c.total_amount), 0.0)).select_from(subquery)
    ) or 0.0
    rows = db.scalars(
        list_filters.apply_sort(stmt, PurchaseOrder, sort)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "total": total,
        "amount_sum": round(float(amount_sum), 2),
        "sort_options": list_filters.options_payload(),
        "items": [serialize_purchase_order(o) for o in rows],
    }


@router.get("/{order_id}", summary="采购订单详情")
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("purchase:view")),
):
    return serialize_purchase_order(_get_order(db, order_id, user))


def _purchase_docx_data(order: PurchaseOrder) -> tuple[dict, list[dict]]:
    supplier = order.supplier
    items = []
    total_quantity = 0
    for item in order.items:
        product = item.product
        total_quantity += item.quantity
        items.append(
            {
                "sku": product.sku if product else "",
                "name": product.name if product else "",
                "spec": product.spec if product else "",
                "unit": product.unit if product else "",
                "category": product.category if product else "",
                "quantity": item.quantity,
                "unit_price": money(item.unit_price),
                "amount": money(item.amount),
            }
        )
    values = {
        "order_no": order.order_no,
        "order_date": order.order_date.strftime("%Y-%m-%d %H:%M") if order.order_date else "",
        "order_day": order.order_date.strftime("%Y-%m-%d") if order.order_date else "",
        "status": order.status,
        "status_text": STATUS_TEXT.get(order.status, order.status),
        "created_by": order.created_by or "",
        "created_at": order.created_at.strftime("%Y-%m-%d %H:%M") if order.created_at else "",
        "supplier_name": supplier.name if supplier else "",
        "supplier_contact": supplier.contact if supplier else "",
        "supplier_phone": supplier.phone if supplier else "",
        "supplier_email": supplier.email if supplier else "",
        "supplier_address": supplier.address if supplier else "",
        "supply_category": supplier.supply_category if supplier else "",
        "item_count": len(order.items),
        "total_quantity": total_quantity,
        "gross_amount": money(order.total_amount),
        "discount_amount": "0.00",
        "total_amount": money(order.total_amount),
        "remark": order.remark or "",
    }
    return values, items


@router.get("/{order_id}/print.docx", summary="生成采购订单 Word 单据")
def print_order_docx(
    order_id: int,
    template_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("purchase:view")),
):
    order = _get_order(db, order_id, user, "打印")
    values, items = _purchase_docx_data(order)
    return build_docx_download(
        db,
        doc_type="purchase_order",
        template_id=template_id,
        values=values,
        items=items,
        default_builder=build_purchase_document,
        default_title="采购订单",
        doc_no=order.order_no,
    )


@router.post("", summary="创建采购订单（草稿）", dependencies=[Depends(require_perm("purchase:manage"))])
def create_order(
    payload: PurchaseOrderCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("purchase:manage")),
):
    supplier = db.get(Supplier, payload.supplier_id)
    if not supplier:
        raise HTTPException(status_code=400, detail="供应商不存在")
    if not payload.items:
        raise HTTPException(status_code=400, detail="采购明细不能为空")

    order = PurchaseOrder(
        order_no=gen_order_no(db, PurchaseOrder, "PO"),
        supplier_id=payload.supplier_id,
        order_date=payload.order_date or datetime.now(),
        tax_rate=max(payload.tax_rate, 0),
        remark=payload.remark,
        photo_path=payload.photo_path,
        photo_name=payload.photo_name,
        photo_paths=json.dumps(
            [page.model_dump() for page in payload.photo_paths], ensure_ascii=False
        ),
        ocr_status=payload.ocr_status,
        ocr_raw=payload.ocr_raw,
        created_by=user.username,
        status="draft",
    )
    total = 0.0
    for item in payload.items:
        product = db.get(Product, item.product_id)
        if not product:
            raise HTTPException(status_code=400, detail=f"商品 {item.product_id} 不存在")
        ensure_product_type(product, "raw_material", "采购订单")
        amount = round(item.quantity * item.unit_price, 2)
        total += amount
        order.items.append(
            PurchaseOrderItem(
                product_id=item.product_id,
                quantity=item.quantity,
                unit_price=item.unit_price,
                amount=amount,
            )
        )
    order.total_amount = round(total, 2)
    _learn_aliases(db, payload.items, payload.raw_supplier_name, supplier)
    db.add(order)
    db.commit()
    db.refresh(order)
    record(
        username=user.username,
        role=user.role,
        action="创建采购订单",
        resource=f"采购单 {order.order_no}",
        detail=f"供应商 {supplier.name}，金额 {order.total_amount:,.2f}",
        request=request,
    )
    return serialize_purchase_order(order)


PURCHASE_ACTION_LABELS = {
    "submit": "提交审批",
    "approve": "审批通过",
    "reject": "审批驳回",
    "order": "下单给供应商",
    "receive": "确认入库",
    "cancel": "取消采购单",
}


@router.put("/{order_id}", summary="编辑草稿采购单", dependencies=[Depends(require_perm("purchase:manage"))])
def update_order(
    order_id: int,
    payload: PurchaseOrderUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("purchase:manage")),
):
    """只允许编辑草稿单：已下单的采购单可能已经发货，改动要走「取消 + 重开」。"""
    order = _get_order(db, order_id, user, "编辑")
    if order.status != "draft":
        raise HTTPException(status_code=400, detail="只有草稿采购单可以编辑，已下单的请先取消")

    data = payload.model_dump(exclude_unset=True)

    supplier = db.get(Supplier, order.supplier_id)
    if "supplier_id" in data:
        supplier = db.get(Supplier, data["supplier_id"])
        if not supplier:
            raise HTTPException(status_code=400, detail="供应商不存在")
        order.supplier_id = supplier.id
    if data.get("order_date"):
        order.order_date = data["order_date"]
    if "remark" in data:
        order.remark = data["remark"] or ""
    if "tax_rate" in data:
        order.tax_rate = max(float(data["tax_rate"] or 0), 0)
    if "photo_path" in data:
        order.photo_path = data["photo_path"] or ""
    if "photo_name" in data:
        order.photo_name = data["photo_name"] or ""
    if data.get("photo_paths") is not None:
        order.photo_paths = json.dumps(data["photo_paths"], ensure_ascii=False)
    if "ocr_status" in data:
        order.ocr_status = data["ocr_status"] or ""
    if "ocr_raw" in data:
        order.ocr_raw = data["ocr_raw"] or ""

    if "items" in data:
        items = data["items"] or []
        if not items:
            raise HTTPException(status_code=400, detail="采购明细不能为空")
        order.items.clear()  # delete-orphan 会删除被移除的明细行
        for item in items:
            product = db.get(Product, item["product_id"])
            if not product:
                raise HTTPException(status_code=400, detail=f"商品 {item['product_id']} 不存在")
            ensure_product_type(product, "raw_material", "采购订单")
            order.items.append(
                PurchaseOrderItem(
                    product_id=item["product_id"],
                    quantity=item["quantity"],
                    unit_price=item["unit_price"],
                    amount=round(item["quantity"] * item["unit_price"], 2),
                )
            )

    if supplier:
        _learn_aliases(
            db, data.get("items") or [], data.get("raw_supplier_name") or "", supplier
        )
    db.flush()
    order.total_amount = round(sum(item.amount for item in order.items), 2)
    db.commit()
    db.refresh(order)
    record(
        username=user.username,
        role=user.role,
        action="编辑采购单",
        resource=f"采购单 {order.order_no}",
        detail=f"金额 {order.total_amount:,.2f}，明细 {len(order.items)} 行",
        request=request,
    )
    return serialize_purchase_order(order)


@router.post("/{order_id}/status", summary="变更采购单状态")
def change_status(
    order_id: int,
    payload: StatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = _get_order(db, order_id, user, "操作")
    action = payload.action
    previous_status = order.status
    required = {
        "submit": "purchase:manage",
        "approve": "purchase:approve",
        "reject": "purchase:approve",
        "order": "purchase:approve",
        "receive": "purchase:receive",
        "cancel": "purchase:manage",
    }.get(action, "purchase:manage")
    # 审批/驳回的级别权限由审批规则判定（默认仍是 purchase:approve），
    # 这样才能把第二级交给财务等其它角色
    if action not in ("approve", "reject") and not has_permission(user.role, required):
        raise HTTPException(status_code=403, detail=f"当前角色无此操作权限（{required}）")

    if action == "submit":
        approvals.ensure_status(order, ("draft",), "提交审批")
        # 按金额匹配审批规则；没有规则时仍是单级审批
        order.approval_required = approval_flow.required_levels(
            db, "purchase", order.total_amount, approval_flow.context_for(db, "purchase", order)
        )
        order.approval_approved = 0
        order.status = "pending"
        approvals.log_action(db, "purchase", order.id, "submit", user, payload.comment)

    elif action == "approve":
        approvals.ensure_status(order, ("pending",), "审批通过")
        result = approval_flow.perform_approval(db, "purchase", order, user, payload.comment)
        if result["completed"]:
            order.status = "ordered"
        else:
            # 会签未齐或还有下一级：保持待审批，返回审批链
            db.commit()
            db.refresh(order)
            data = serialize_purchase_order(order)
            data["approval"] = approval_flow.describe(
                db, "purchase", order.total_amount, order.approval_approved, order_id=order.id,
                ctx=approval_flow.context_for(db, "purchase", order),
            )
            data["pending_next_level"] = result["level_complete"]
            record(
                username=user.username,
                role=user.role,
                action="采购订单审批通过",
                resource=f"采购订单 {order.order_no}",
                detail=(
                    f"第 {result['level']}/{result['required']} 级"
                    + ("" if result["level_complete"] else "（会签未齐）")
                ),
                request=request,
            )
            return data

    elif action == "reject":
        approvals.ensure_status(order, ("pending",), "审批驳回")
        level, _rule = approval_flow.ensure_level_permission(db, "purchase", order, user)
        order.status = "draft"
        order.approval_approved = 0
        approvals.log_action(
            db, "purchase", order.id, "reject", user, payload.comment or "驳回", level=level
        )

    elif action == "order":
        # 兼容旧接口：有审批权限的人可跳过审批直接下单
        approvals.ensure_status(order, ("draft",), "下单")
        order.status = "ordered"

    elif action == "receive":
        if order.status != "ordered":
            raise HTTPException(status_code=400, detail="只有已下单采购单可以入库")
        for item in order.items:
            ensure_product_type(item.product, "raw_material", "采购入库")
            # 采购入库默认进入默认仓库的第一个库位
            apply_stock_change(
                db,
                item.product,
                "in",
                item.quantity,
                reason=f"采购入库 {order.order_no}",
                biz_type="采购入库",
                ref_no=order.order_no,
                username=user.username,
                unit_cost=item.unit_price,
            )
        order.status = "received"

    elif action == "cancel":
        if order.status == "cancelled":
            raise HTTPException(status_code=400, detail="采购单已取消")
        if order.status == "received":
            shortages = [
                item.product.name
                for item in order.items
                if item.product.stock_qty < item.quantity
            ]
            if shortages:
                raise HTTPException(
                    status_code=400,
                    detail=f"库存已被消耗，无法撤销入库：{'、'.join(shortages)}",
                )
            for item in order.items:
                deduct_stock(
                    db,
                    item.product,
                    item.quantity,
                    reason=f"采购入库撤销 {order.order_no}",
                    biz_type="采购入库撤销",
                    ref_no=order.order_no,
                    username=user.username,
                )
        order.status = "cancelled"

    else:
        raise HTTPException(status_code=400, detail=f"不支持的操作：{action}")

    db.commit()
    db.refresh(order)
    record(
        username=user.username,
        role=user.role,
        action="采购单状态变更",
        resource=f"采购单 {order.order_no}",
        detail=PURCHASE_ACTION_LABELS.get(action, action),
        request=request,
    )
    data = serialize_purchase_order(order)
    if action == "cancel" and previous_status == "received":
        # 撤销入库导致库存减少，检查自动预警
        data["auto_alert"] = alerts.check_and_notify(
            db, [item.product_id for item in order.items], user.username, scene=f"采购入库撤销 {order.order_no}"
        )
    return data


@router.get("/{order_id}/approvals", summary="采购订单审批轨迹")
def order_approvals(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("purchase:view")),
):
    order = _get_order(db, order_id, user)
    return [
        {
            "id": row.id,
            "action": row.action,
            "operator": row.operator,
            "principal": row.principal or row.operator,
            "role": row.role,
            "level": row.level,
            "comment": row.comment,
            "created_at": row.created_at,
        }
        for row in approvals.order_logs(db, "purchase", order.id)
    ]


@router.delete("/{order_id}", summary="删除草稿采购单", dependencies=[Depends(require_perm("purchase:manage"))])
def delete_order(
    order_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("purchase:manage")),
):
    order = _get_order(db, order_id, user, "删除")
    if order.status != "draft":
        raise HTTPException(status_code=400, detail="仅草稿采购单可删除")
    order_no = order.order_no
    db.delete(order)
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="删除采购单",
        resource=f"采购单 {order_no}",
        detail="删除草稿采购单",
        request=request,
    )
    return {"ok": True}
