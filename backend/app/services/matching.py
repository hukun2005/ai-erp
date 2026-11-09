"""发票三单匹配：采购订单 ↔ 入库单 ↔ 进项发票 自动比对。

以采购订单为枢纽，汇总：采购数量/金额/税额、入库数量/金额、进项发票金额/税额，
逐单比对数量、单价、税额、金额差异并预警；缺入库单/进项发票也作为差异标出。
"""
from sqlalchemy.orm import Session

from ..models import InboundOrder, Invoice, PurchaseOrder

PO_STATUS = ("received", "ordered")  # 需要三单匹配的在途/已入库采购单
INBOUND_OK = ("confirmed",)


def _split_tax(gross: float, rate: float) -> tuple[float, float]:
    rate = float(rate or 0)
    gross = float(gross or 0)
    if rate <= 0 or gross <= 0:
        return round(gross, 2), 0.0
    net = round(gross / (1 + rate / 100), 2)
    return net, round(gross - net, 2)


def _avg_price(items) -> float:
    qty = sum(getattr(i, "quantity", 0) or 0 for i in items)
    if qty <= 0:
        return 0.0
    return round(sum((getattr(i, "unit_price", 0.0) or 0.0) * (getattr(i, "quantity", 0) or 0) for i in items) / qty, 2)


def three_way_match(db: Session) -> dict:
    rows: list[dict] = []
    pos = db.query(PurchaseOrder).filter(PurchaseOrder.status.in_(PO_STATUS)).order_by(PurchaseOrder.id.desc()).all()

    for po in pos:
        po_qty = sum(i.quantity or 0 for i in po.items)
        po_amount = round(float(po.total_amount or 0), 2)
        _net, po_tax = _split_tax(po.total_amount, po.tax_rate)
        po_avg_price = _avg_price(po.items)

        inbounds = (
            db.query(InboundOrder)
            .filter(InboundOrder.purchase_order_id == po.id, InboundOrder.status.in_(INBOUND_OK))
            .all()
        )
        inb_items = [ii for ib in inbounds for ii in ib.items]
        inb_qty = sum(ii.quantity or 0 for ii in inb_items)
        inb_amount = round(sum(float(ib.total_amount or 0) for ib in inbounds), 2)
        inb_avg_price = _avg_price(inb_items)

        invoices = (
            db.query(Invoice)
            .filter(
                Invoice.direction == "purchase",
                Invoice.order_no == po.order_no,
                Invoice.status != "voided",
            )
            .all()
        )
        inv_amount = round(sum(float(i.total_amount or 0) for i in invoices), 2)
        inv_tax = round(sum(float(i.tax_amount or 0) for i in invoices), 2)

        diffs: list[dict] = []

        if inbounds and inb_qty != po_qty:
            diffs.append({"field": "数量", "detail": f"采购 {po_qty} / 入库 {inb_qty}（差 {inb_qty - po_qty}）"})
        if inbounds and abs(inb_avg_price - po_avg_price) > 0.005:
            diffs.append({"field": "单价", "detail": f"采购 {po_avg_price} / 入库 {inb_avg_price}"})
        if inbounds and abs(inb_amount - po_amount) > 0.005:
            diffs.append({"field": "金额", "detail": f"采购 {po_amount} / 入库 {inb_amount}"})
        if invoices and abs(inv_amount - po_amount) > 0.005:
            diffs.append({"field": "金额", "detail": f"采购 {po_amount} / 发票 {inv_amount}"})
        if invoices and abs(inv_tax - po_tax) > 0.005:
            diffs.append({"field": "税额", "detail": f"采购单 {po_tax} / 发票 {inv_tax}"})
        if not inbounds:
            diffs.append({"field": "缺单", "detail": "未匹配到已确认入库单"})
        if not invoices:
            diffs.append({"field": "缺单", "detail": "未匹配到进项发票"})

        rows.append(
            {
                "order_id": po.id,
                "order_no": po.order_no,
                "supplier": po.supplier.name if po.supplier else "",
                "po_status": po.status,
                "po_qty": po_qty,
                "po_amount": po_amount,
                "po_tax": po_tax,
                "inbound_qty": inb_qty,
                "inbound_amount": inb_amount,
                "inbound_no": "、".join(ib.inbound_no for ib in inbounds),
                "invoice_amount": inv_amount,
                "invoice_tax": inv_tax,
                "invoice_no": "、".join(i.invoice_no for i in invoices),
                "matched": not diffs,
                "diffs": diffs,
            }
        )

    matched = sum(1 for r in rows if r["matched"])
    return {
        "total": len(rows),
        "matched": matched,
        "warn": len(rows) - matched,
        "items": rows,
    }