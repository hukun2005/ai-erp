"""把业务单据拍平成 PDF 渲染用的版式数据。

PDF 渲染不直接碰 ORM，这里统一取数，保证不同单据的字段口径一致。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import InboundOrder, OutboundOrder, Product, PurchaseOrder, SalesOrder

DOC_TYPES = {
    "sales_order": "销售订单",
    "purchase_order": "采购订单",
    "inbound": "入库单",
    "outbound": "出库单",
}

STATUS_TEXT = {
    "draft": "草稿",
    "pending": "待审批",
    "confirmed": "已确认",
    "ordered": "已下单",
    "received": "已入库",
    "completed": "已完成",
    "cancelled": "已取消",
    "shipped": "已出库",
    "signed": "已签收",
}


def _time(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


def _day(value) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def _product(item) -> Product | None:
    return item.product


def _item_columns(with_batch: bool = False) -> list[dict]:
    columns = [
        {"key": "sku", "label": "商品编码", "width": 1.2},
        {"key": "name", "label": "商品名称", "width": 2.4},
        {"key": "spec", "label": "规格", "width": 1.0},
        {"key": "unit", "label": "单位", "width": 0.6, "align": "center"},
        {"key": "quantity", "label": "数量", "width": 0.8, "align": "right"},
        {"key": "unit_price", "label": "单价", "width": 1.0, "align": "right", "format": "money"},
        {"key": "amount", "label": "金额", "width": 1.2, "align": "right", "format": "money"},
    ]
    if with_batch:
        columns.insert(4, {"key": "batch_no", "label": "批次", "width": 1.1})
        columns.insert(5, {"key": "expiry_date", "label": "效期", "width": 1.0, "align": "center"})
    return columns


def build_spec(db: Session, doc_type: str, doc_id: int) -> dict:
    """按单据类型取数，返回渲染规格；不存在的单据返回空字典即可。"""
    if doc_type not in DOC_TYPES:
        return {}

    if doc_type == "sales_order":
        order = db.get(SalesOrder, doc_id)
        if not order:
            return {}
        customer = order.customer
        rows = []
        for item in order.items:
            product = _product(item)
            rows.append(
                {
                    "sku": product.sku if product else "",
                    "name": product.name if product else "",
                    "spec": product.spec if product else "",
                    "unit": product.unit if product else "",
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "amount": item.amount,
                }
            )
        gross = sum(item.amount for item in order.items)
        return {
            "doc_type": doc_type,
            "doc_no": order.order_no,
            "title": "销售订单",
            "meta": [
                ("客户", customer.name if customer else ""),
                ("联系人", customer.contact if customer else ""),
                ("联系电话", customer.phone if customer else ""),
                ("下单日期", _day(order.order_date)),
                ("订单状态", STATUS_TEXT.get(order.status, order.status)),
                ("制单人", order.created_by),
                ("收货地址", customer.address if customer else ""),
            ],
            "columns": _item_columns(),
            "rows": rows,
            "totals": [
                ("商品金额", f"¥ {gross:,.2f}", False),
                ("优惠金额", f"¥ {float(order.discount_amount or 0):,.2f}", False),
                ("应收合计", f"¥ {float(order.total_amount or 0):,.2f}", True),
            ],
            "remark": order.remark,
            "signs": ["制单", "审核", "客户签收"],
        }

    if doc_type == "purchase_order":
        order = db.get(PurchaseOrder, doc_id)
        if not order:
            return {}
        supplier = order.supplier
        rows = []
        for item in order.items:
            product = _product(item)
            rows.append(
                {
                    "sku": product.sku if product else "",
                    "name": product.name if product else "",
                    "spec": product.spec if product else "",
                    "unit": product.unit if product else "",
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "amount": item.amount,
                }
            )
        return {
            "doc_type": doc_type,
            "doc_no": order.order_no,
            "title": "采购订单",
            "meta": [
                ("供应商", supplier.name if supplier else ""),
                ("联系人", supplier.contact if supplier else ""),
                ("联系电话", supplier.phone if supplier else ""),
                ("下单日期", _day(order.order_date)),
                ("订单状态", STATUS_TEXT.get(order.status, order.status)),
                ("制单人", order.created_by),
            ],
            "columns": _item_columns(),
            "rows": rows,
            "totals": [("采购金额", f"¥ {float(order.total_amount or 0):,.2f}", True)],
            "remark": order.remark,
            "signs": ["制单", "审核", "供应商确认"],
        }

    if doc_type == "inbound":
        order = db.get(InboundOrder, doc_id)
        if not order:
            return {}
        rows = []
        for item in order.items:
            product = _product(item)
            rows.append(
                {
                    "sku": product.sku if product else "",
                    "name": product.name if product else "",
                    "spec": product.spec if product else "",
                    "unit": product.unit if product else "",
                    "batch_no": item.batch_no or "",
                    "expiry_date": _day(item.expiry_date) if item.expiry_date else "",
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "amount": item.amount,
                }
            )
        return {
            "doc_type": doc_type,
            "doc_no": order.inbound_no,
            "title": "入库单",
            "meta": [
                ("供应商", order.supplier.name if order.supplier else ""),
                ("入库仓库", order.warehouse.name if order.warehouse else ""),
                ("库位", order.location.code if order.location else ""),
                ("入库日期", _day(order.receipt_date)),
                ("状态", STATUS_TEXT.get(order.status, order.status)),
                ("制单人", order.created_by),
            ],
            "columns": _item_columns(with_batch=True),
            "rows": rows,
            "totals": [("入库金额", f"¥ {float(order.total_amount or 0):,.2f}", True)],
            "remark": order.remark,
            "signs": ["制单", "验收", "仓管"],
        }

    order = db.get(OutboundOrder, doc_id)
    if not order:
        return {}
    rows = []
    for item in order.items:
        product = _product(item)
        rows.append(
            {
                "sku": product.sku if product else "",
                "name": product.name if product else "",
                "spec": product.spec if product else "",
                "unit": product.unit if product else "",
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "amount": item.amount,
            }
        )
    return {
        "doc_type": doc_type,
        "doc_no": order.outbound_no,
        "title": "销售出库单",
        "meta": [
            ("客户", order.customer.name if order.customer else ""),
            ("销售订单", order.sales_order.order_no if order.sales_order else ""),
            ("发货仓库", order.warehouse.name if order.warehouse else ""),
            ("物流", f"{order.logistics_company} {order.logistics_no}".strip()),
            ("发货日期", _day(order.ship_date)),
            ("状态", STATUS_TEXT.get(order.status, order.status)),
            ("制单人", order.created_by),
        ],
        "columns": _item_columns(),
        "rows": rows,
        "totals": [("合计金额", f"¥ {float(order.total_amount or 0):,.2f}", True)],
        "remark": order.remark,
        "signs": ["发货人", "承运人", "客户签收"],
    }
