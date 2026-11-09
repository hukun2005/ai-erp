from datetime import datetime
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import PurchaseOrder, SalesOrder


def photo_pages(raw: str) -> list[dict]:
    """把 photo_paths 里存的 JSON 还原成照片列表，坏数据按空处理。"""
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [
        {"path": str(item.get("path", "")), "name": str(item.get("name", ""))}
        for item in data
        if isinstance(item, dict) and item.get("path")
    ]


def gen_order_no(db: Session, model, prefix: str, field: str = "order_no") -> str:
    """生成形如 SO202611010001 的单号；退货/盘点等单据用 field 指定自己的单号列。

    序号取「当日已有单号的最大值 + 1」，而不是记录条数：草稿单允许删除，
    条数会因此变小，按条数生成就会与仍在库里的单号相撞，直接触发唯一约束。
    """
    head = f"{prefix}{datetime.now().strftime('%Y%m%d')}"
    column = getattr(model, field)
    values = db.scalars(select(column).where(column.like(f"{head}%"))).all()
    max_seq = 0
    for value in values:
        suffix = str(value or "")[len(head):]
        if suffix.isdigit():
            max_seq = max(max_seq, int(suffix))
    return f"{head}{max_seq + 1:04d}"


def serialize_sales_order(order: SalesOrder) -> dict:
    customer = order.customer
    return {
        "id": order.id,
        "order_no": order.order_no,
        "customer_id": order.customer_id,
        "customer_name": customer.name if customer else "",
        # 详情页需要展示客户与商品的完整档案，列表页不使用这些字段
        "customer_contact": customer.contact if customer else "",
        "customer_phone": customer.phone if customer else "",
        "customer_email": customer.email if customer else "",
        "customer_address": customer.address if customer else "",
        "customer_level": customer.level if customer else "",
        "customer_credit_limit": customer.credit_limit if customer else 0,
        "status": order.status,
        "order_date": order.order_date,
        "created_at": order.created_at,
        "total_amount": order.total_amount,
        "discount_amount": order.discount_amount,
        "tax_rate": float(order.tax_rate or 0),
        "remark": order.remark,
        "photo_path": order.photo_path or "",
        "photo_name": order.photo_name or "",
        "photo_paths": photo_pages(order.photo_paths),
        "ocr_status": order.ocr_status or "",
        "ocr_raw": order.ocr_raw or "",
        "created_by": order.created_by,
        # 多级审批进度：0 表示未配置规则（单级流程）
        "approval_required": int(order.approval_required or 0),
        "approval_approved": int(order.approval_approved or 0),
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product.name if item.product else "",
                "product_sku": item.product.sku if item.product else "",
                "product_spec": item.product.spec if item.product else "",
                "product_unit": item.product.unit if item.product else "",
                "product_category": item.product.category if item.product else "",
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "amount": item.amount,
            }
            for item in order.items
        ],
    }


def serialize_purchase_order(order: PurchaseOrder) -> dict:
    return {
        "id": order.id,
        "order_no": order.order_no,
        "supplier_id": order.supplier_id,
        "supplier_name": order.supplier.name if order.supplier else "",
        "status": order.status,
        "order_date": order.order_date,
        "total_amount": order.total_amount,
        "tax_rate": float(order.tax_rate or 0),
        "remark": order.remark,
        "photo_path": order.photo_path or "",
        "photo_name": order.photo_name or "",
        "photo_paths": photo_pages(order.photo_paths),
        "ocr_status": order.ocr_status or "",
        "ocr_raw": order.ocr_raw or "",
        "created_by": order.created_by,
        "approval_required": int(order.approval_required or 0),
        "approval_approved": int(order.approval_approved or 0),
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product.name if item.product else "",
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "amount": item.amount,
            }
            for item in order.items
        ],
    }
