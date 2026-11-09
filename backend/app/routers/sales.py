from datetime import datetime
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import list_filters
from ..audit import record
from ..config import settings
from ..database import get_db
from ..deps import require_perm
from ..models import Customer, Product, SalesOrder, SalesOrderItem, User
from ..permissions import has_permission
from ..schemas import SalesOrderCreate, SalesOrderUpdate, StatusUpdate
from ..scoping import apply_owner_scope, ensure_owner, own_scope
from ..services import alerts, approvals, credit as credit_service
from ..services import approval_flow, credit_policy, matching
from ..services import outbound as outbound_service
from ..services import reservation
from ..services.docx_render import build_default_document, build_docx_download, money
from ..services.product_type import ensure_product_type
from ..services.stock import apply_stock_change, deduct_stock
from ..utils import gen_order_no, serialize_sales_order

router = APIRouter(prefix="/api/sales-orders", tags=["销售订单"])

STATUS_TEXT = {
    "draft": "草稿",
    "pending": "待审批",
    "confirmed": "已确认",
    "completed": "已完成",
    "cancelled": "已取消",
}


def _get_order(db: Session, order_id: int, user: User, action: str = "查看") -> SalesOrder:
    order = db.get(SalesOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    ensure_owner(order.created_by, user, action)
    return order


def _learn_aliases(db: Session, items, raw_customer_name: str, customer: Customer | None) -> None:
    """把用户确认过的「识别名 → 商品/客户」记下来，下次识别直接命中。"""
    entries = []
    for item in items or []:
        product_id = item.get("product_id") if isinstance(item, dict) else item.product_id
        raw_name = item.get("raw_name") if isinstance(item, dict) else item.raw_name
        if product_id and raw_name:
            entries.append((product_id, raw_name))
    if entries:
        names = matching.product_name_map(db, [product_id for product_id, _ in entries])
        matching.learn_item_aliases(db, entries, names, source="ocr")
    if raw_customer_name and customer:
        matching.learn_alias(
            db,
            "customer",
            customer.id,
            raw_customer_name,
            canonical=customer.name,
            source="ocr",
        )


@router.get("", summary="销售订单列表")
def list_orders(
    keyword: str = Query(""),
    status: str = Query(""),
    customer_id: int | None = Query(None),
    created_by: str = Query(""),
    start_date: str = Query("", description="下单日期起（YYYY-MM-DD，含当天）"),
    end_date: str = Query("", description="下单日期止（YYYY-MM-DD，含当天）"),
    amount_min: float | None = Query(None, description="订单金额下限"),
    amount_max: float | None = Query(None, description="订单金额上限"),
    sort: str = Query("", description="排序：created_desc/date_desc/date_asc/amount_desc/amount_asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("sales:view")),
):
    stmt = select(SalesOrder).join(Customer)
    stmt = apply_owner_scope(stmt, SalesOrder.created_by, user)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(SalesOrder.order_no.like(like), Customer.name.like(like)))
    if status:
        stmt = stmt.where(SalesOrder.status == status)
    if customer_id:
        stmt = stmt.where(SalesOrder.customer_id == customer_id)
    stmt = list_filters.apply_common_filters(
        stmt,
        SalesOrder,
        start_date=start_date,
        end_date=end_date,
        amount_min=amount_min,
        amount_max=amount_max,
        created_by=created_by,
    )
    subquery = stmt.subquery()
    total = db.scalar(select(func.count()).select_from(subquery)) or 0
    # 台账口径：当前筛选条件下的总额，而不是本页合计；必须从子查询取列，
    # 否则会和原表形成笛卡尔积，金额被重复计成 N 倍。
    amount_sum = db.scalar(
        select(func.coalesce(func.sum(subquery.c.total_amount), 0.0)).select_from(subquery)
    ) or 0.0
    rows = db.scalars(
        list_filters.apply_sort(stmt, SalesOrder, sort)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "total": total,
        "amount_sum": round(float(amount_sum), 2),
        "sort_options": list_filters.options_payload(),
        "items": [serialize_sales_order(o) for o in rows],
    }


@router.get("/{order_id}", summary="销售订单详情")
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("sales:view")),
):
    order = _get_order(db, order_id, user)
    data = serialize_sales_order(order)
    data["shipment"] = outbound_service.order_progress(db, order)
    data["outbounds"] = [
        outbound_service.serialize(row, with_items=False)
        for row in sorted(order.outbounds, key=lambda item: item.id)
        if row.status != "cancelled"
    ]
    data["shipping_mode"] = settings.SALES_SHIPPING_MODE
    data["approval"] = approval_flow.describe(
        db, "sales", order.total_amount, order.approval_approved, order_id=order.id,
        ctx=approval_flow.context_for(db, "sales", order),
    )
    override = credit_policy.latest_override(db, order.id)
    data["credit_override"] = (
        {
            "id": override.id,
            "status": override.status,
            "reason": override.reason,
            "over_amount": override.over_amount,
            "requested_by": override.requested_by,
            "requested_at": override.requested_at,
            "decided_by": override.decided_by,
            "comment": override.comment,
        }
        if override is not None
        else None
    )
    return data


def _order_docx_data(order: SalesOrder) -> tuple[dict, list[dict]]:
    """把订单拍平成 Word 占位符能直接使用的键值。"""
    customer = order.customer
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
    gross = round(sum(item.amount for item in order.items), 2)
    values = {
        "order_no": order.order_no,
        "order_date": order.order_date.strftime("%Y-%m-%d %H:%M") if order.order_date else "",
        "order_day": order.order_date.strftime("%Y-%m-%d") if order.order_date else "",
        "status": order.status,
        "status_text": STATUS_TEXT.get(order.status, order.status),
        "created_by": order.created_by or "",
        "created_at": order.created_at.strftime("%Y-%m-%d %H:%M") if order.created_at else "",
        "customer_name": customer.name if customer else "",
        "customer_contact": customer.contact if customer else "",
        "customer_phone": customer.phone if customer else "",
        "customer_email": customer.email if customer else "",
        "customer_address": customer.address if customer else "",
        "customer_level": customer.level if customer else "",
        "customer_credit_limit": money(customer.credit_limit) if customer else "0.00",
        "item_count": len(order.items),
        "total_quantity": total_quantity,
        "gross_amount": money(gross),
        "discount_amount": money(order.discount_amount),
        "total_amount": money(order.total_amount),
        "remark": order.remark or "",
    }
    return values, items


@router.get("/{order_id}/print.docx", summary="生成销售订单 Word 单据")
def print_order_docx(
    order_id: int,
    template_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("sales:view")),
):
    order = _get_order(db, order_id, user, "打印")
    values, items = _order_docx_data(order)
    return build_docx_download(
        db,
        doc_type="sales_order",
        template_id=template_id,
        values=values,
        items=items,
        default_builder=build_default_document,
        default_title="销售订单",
        doc_no=order.order_no,
    )


@router.get("/{order_id}/delivery.docx", summary="生成销售送货单 Word 单据")
def print_delivery_docx(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("sales:view")),
):
    order = _get_order(db, order_id, user, "打印")
    values, items = _order_docx_data(order)
    return build_docx_download(
        db,
        doc_type="sales_delivery",
        template_id=None,
        values=values,
        items=items,
        default_builder=build_default_document,
        default_title="送货单",
        doc_no=order.order_no,
    )


@router.post("", summary="创建销售订单（草稿）", dependencies=[Depends(require_perm("sales:manage"))])
def create_order(
    payload: SalesOrderCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("sales:manage")),
):
    customer = db.get(Customer, payload.customer_id)
    if not customer:
        raise HTTPException(status_code=400, detail="客户不存在")
    # 数据范围：销售员只能给本人负责的客户下单；公海客户可领取
    if own_scope(user.role):
        if customer.owner and customer.owner != user.username:
            raise HTTPException(status_code=403, detail="数据范围限制：该客户由其他销售负责")
        customer.owner = user.username
    if not payload.items:
        raise HTTPException(status_code=400, detail="订单明细不能为空")

    order = SalesOrder(
        order_no=gen_order_no(db, SalesOrder, "SO"),
        customer_id=payload.customer_id,
        order_date=payload.order_date or datetime.now(),
        discount_amount=max(payload.discount_amount, 0),
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
        ensure_product_type(product, "finished_good", "销售订单")
        amount = round(item.quantity * item.unit_price, 2)
        total += amount
        order.items.append(
            SalesOrderItem(
                product_id=item.product_id,
                quantity=item.quantity,
                unit_price=item.unit_price,
                amount=amount,
            )
        )
    order.total_amount = round(total - order.discount_amount, 2)
    _learn_aliases(db, payload.items, payload.raw_customer_name, customer)
    db.add(order)
    db.commit()
    db.refresh(order)
    record(
        username=user.username,
        role=user.role,
        action="创建销售订单",
        resource=f"销售订单 {order.order_no}",
        detail=f"客户 {customer.name}，金额 {order.total_amount:,.2f}",
        request=request,
    )
    return serialize_sales_order(order)


STATUS_ACTION_LABELS = {
    "submit": "提交审批",
    "approve": "审批通过",
    "reject": "审批驳回",
    "confirm": "确认订单",
    "complete": "完成订单",
    "cancel": "取消订单",
}


def _require(action: str, permission: str, user: User) -> None:
    if not has_permission(user.role, permission):
        raise HTTPException(status_code=403, detail=f"当前角色无此操作权限（{permission}）")


def _confirm_stock(db: Session, order: SalesOrder, user: User) -> list[str]:
    """确认订单；出库模式下只确认单据，库存由出库单扣减。

    返回需要提示给用户的信用告警。
    """
    for item in order.items:
        ensure_product_type(item.product, "finished_good", "销售出库")
    # 信用校验放在扣库存之前：block 策略下超额订单不允许出库
    credit = credit_service.check_order(db, order)
    if credit.get("blocked"):
        # 已批准的特批可以放行这一单，其余情况一律拦截
        override = credit_policy.approved_override(db, order.id)
        if override is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    credit["message"]
                    + "；请先回收欠款、调整信用额度，或在订单详情里申请「超额特批」"
                ),
            )

    if settings.SALES_SHIPPING_MODE == "direct":
        # 兼容旧流程：审批通过即出库扣减库存
        shortages = [
            f"{item.product.name}（需 {item.quantity}，库存 {item.product.stock_qty}）"
            for item in order.items
            if item.product.stock_qty < item.quantity
        ]
        if shortages:
            raise HTTPException(status_code=400, detail="库存不足：" + "；".join(shortages))
        for item in order.items:
            deduct_stock(
                db,
                item.product,
                item.quantity,
                reason=f"销售订单 {order.order_no} 出库",
                biz_type="销售出库",
                ref_no=order.order_no,
                username=user.username,
            )
    else:
        # 出库模式：确认订单只占用可用量，库存等出库单确认时才扣
        reservation.reserve_for_order(db, order, user)
    order.status = "confirmed"
    return [credit["message"]] if credit.get("warn") else []


@router.post("/{order_id}/status", summary="变更订单状态（提交/审批/驳回/完成/取消）")
def change_status(
    order_id: int,
    payload: StatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("sales:view")),
):
    order = _get_order(db, order_id, user, "操作")
    action = payload.action
    warnings: list[str] = []

    if action == "submit":
        _require(action, "sales:manage", user)
        approvals.ensure_status(order, ("draft",), "提交审批")
        # 按金额匹配审批规则：命中几条就走几级，没有规则时仍是一级
        order.approval_required = approval_flow.required_levels(
            db, "sales", order.total_amount, approval_flow.context_for(db, "sales", order)
        )
        order.approval_approved = 0
        order.status = "pending"
        approvals.log_action(db, "sales", order.id, "submit", user, payload.comment)

    elif action == "approve":
        approvals.ensure_status(order, ("pending",), "审批通过")
        # 级别权限由审批规则判定（默认仍是 sales:approve），支持会签/或签与委托代理
        result = approval_flow.perform_approval(db, "sales", order, user, payload.comment)
        if result["completed"]:
            warnings = _confirm_stock(db, order, user)
        else:
            # 会签未齐或还有下一级：保持待审批，返回审批链
            db.commit()
            db.refresh(order)
            data = serialize_sales_order(order)
            data["approval"] = approval_flow.describe(
                db, "sales", order.total_amount, order.approval_approved, order_id=order.id,
                ctx=approval_flow.context_for(db, "sales", order),
            )
            data["pending_next_level"] = result["level_complete"]
            record(
                username=user.username,
                role=user.role,
                action="销售订单审批通过",
                resource=f"销售订单 {order.order_no}",
                detail=(
                    f"第 {result['level']}/{result['required']} 级"
                    + ("" if result["level_complete"] else "（会签未齐）")
                ),
                request=request,
            )
            return data

    elif action == "reject":
        approvals.ensure_status(order, ("pending",), "审批驳回")
        level, _rule = approval_flow.ensure_level_permission(db, "sales", order, user)
        order.status = "draft"
        order.approval_approved = 0
        approvals.log_action(
            db, "sales", order.id, "reject", user, payload.comment or "驳回", level=level
        )

    elif action == "confirm":
        # 兼容旧接口：有审批权限的人可跳过审批直接确认
        _require(action, "sales:approve", user)
        approvals.ensure_status(order, ("draft",), "确认订单")
        warnings = _confirm_stock(db, order, user)

    elif action == "complete":
        _require(action, "sales:manage", user)
        if order.status != "confirmed":
            raise HTTPException(status_code=400, detail="只有已确认订单可以完成")
        order.status = "completed"

    elif action == "cancel":
        _require(action, "sales:manage", user)
        if order.status == "cancelled":
            raise HTTPException(status_code=400, detail="订单已取消")
        # 出库模式下库存已由出库单扣减，订单不能绕过出库单直接取消
        open_outbounds = [
            row for row in order.outbounds if row.status in ("draft", "shipped", "signed")
        ]
        if open_outbounds:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"该订单还有 {len(open_outbounds)} 张出库单（{open_outbounds[0].outbound_no} 等），"
                    "请先取消或在出库单里回库后再取消订单"
                ),
            )
        if order.status in ("confirmed", "completed") and settings.SALES_SHIPPING_MODE == "direct":
            for item in order.items:
                # 旧流程取消订单回库：退回默认仓库的第一个库位
                apply_stock_change(
                    db,
                    item.product,
                    "in",
                    item.quantity,
                    reason=f"销售订单 {order.order_no} 取消回库",
                    biz_type="销售取消回库",
                    ref_no=order.order_no,
                    username=user.username,
                )
        elif settings.SALES_SHIPPING_MODE == "outbound":
            # 出库模式下把已占用的库存释放掉
            reservation.release_for_order(db, order)
        order.status = "cancelled"

    else:
        raise HTTPException(status_code=400, detail=f"不支持的操作：{action}")

    db.commit()
    db.refresh(order)
    record(
        username=user.username,
        role=user.role,
        action="销售订单状态变更",
        resource=f"销售订单 {order.order_no}",
        detail=STATUS_ACTION_LABELS.get(action, action) + (f"：{payload.comment}" if payload.comment else ""),
        request=request,
    )
    data = serialize_sales_order(order)
    if settings.SALES_SHIPPING_MODE == "outbound":
        data["shipment"] = outbound_service.order_progress(db, order)
    if warnings:
        data["credit_warning"] = "；".join(warnings)
    if action in ("approve", "confirm"):
        # 销售出库后检查自动预警
        data["auto_alert"] = alerts.check_and_notify(
            db, [item.product_id for item in order.items], user.username, scene=f"销售出库 {order.order_no}"
        )
    return data


@router.get("/{order_id}/approvals", summary="销售订单审批轨迹")
def order_approvals(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("sales:view")),
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
        for row in approvals.order_logs(db, "sales", order.id)
    ]


@router.put("/{order_id}", summary="编辑草稿销售订单", dependencies=[Depends(require_perm("sales:manage"))])
def update_order(
    order_id: int,
    payload: SalesOrderUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("sales:manage")),
):
    """只允许编辑草稿单：已确认订单的库存已经扣减，改动必须走「取消 + 重开」。"""
    order = _get_order(db, order_id, user, "编辑")
    if order.status != "draft":
        raise HTTPException(status_code=400, detail="只有草稿订单可以编辑，已确认订单请先取消")

    data = payload.model_dump(exclude_unset=True)

    customer = db.get(Customer, order.customer_id)
    if "customer_id" in data:
        customer = db.get(Customer, data["customer_id"])
        if not customer:
            raise HTTPException(status_code=400, detail="客户不存在")
        if own_scope(user.role) and customer.owner and customer.owner != user.username:
            raise HTTPException(status_code=403, detail="数据范围限制：该客户由其他销售负责")
        order.customer_id = customer.id
    if data.get("order_date"):
        order.order_date = data["order_date"]
    if "remark" in data:
        order.remark = data["remark"] or ""
    if "discount_amount" in data:
        order.discount_amount = max(data["discount_amount"] or 0, 0)
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
            raise HTTPException(status_code=400, detail="订单明细不能为空")
        order.items.clear()  # delete-orphan 会删除被移除的明细行
        for item in items:
            product = db.get(Product, item["product_id"])
            if not product:
                raise HTTPException(status_code=400, detail=f"商品 {item['product_id']} 不存在")
            ensure_product_type(product, "finished_good", "销售订单")
            order.items.append(
                SalesOrderItem(
                    product_id=item["product_id"],
                    quantity=item["quantity"],
                    unit_price=item["unit_price"],
                    amount=round(item["quantity"] * item["unit_price"], 2),
                )
            )

    _learn_aliases(
        db, data.get("items") or [], data.get("raw_customer_name") or "", customer
    )
    db.flush()
    order.total_amount = round(sum(item.amount for item in order.items) - order.discount_amount, 2)
    db.commit()
    db.refresh(order)
    record(
        username=user.username,
        role=user.role,
        action="编辑销售订单",
        resource=f"销售订单 {order.order_no}",
        detail=f"金额 {order.total_amount:,.2f}，明细 {len(order.items)} 行",
        request=request,
    )
    return serialize_sales_order(order)




@router.delete("/{order_id}", summary="删除草稿订单", dependencies=[Depends(require_perm("sales:manage"))])
def delete_order(
    order_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("sales:manage")),
):
    order = _get_order(db, order_id, user, "删除")
    if order.status != "draft":
        raise HTTPException(status_code=400, detail="仅草稿订单可删除，已确认订单请先取消")
    order_no = order.order_no
    db.delete(order)
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="删除销售订单",
        resource=f"销售订单 {order_no}",
        detail="删除草稿订单",
        request=request,
    )
    return {"ok": True}
