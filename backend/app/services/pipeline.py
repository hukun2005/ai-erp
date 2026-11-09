"""报价 → 订单 → 出库 → 开票 → 收款 全链路状态看板。

以销售订单为枢纽，把每条链路节点的完成度拉成一张表，定位订单卡在哪一环。
"""
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Invoice, Payment, Quotation, SalesOrder

ORDER_DONE = ("confirmed", "completed")
SHIPPED = ("shipped", "signed")


def _customer_paid(db: Session, customer_id: int) -> float:
    return round(
        float(
            db.scalar(
                db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
                    Payment.customer_id == customer_id, Payment.status == "posted"
                )
            )
            or 0.0
        ),
        2,
    )


def _stage(key, label, status, text):
    return {"key": key, "label": label, "status": status, "text": text}


def sales_pipeline(db: Session) -> dict:
    orders = (
        db.query(SalesOrder)
        .filter(SalesOrder.status.in_(("pending", "confirmed", "completed")))
        .order_by(SalesOrder.id.desc())
        .all()
    )

    rows: list[dict] = []
    stuck = {"order": 0, "outbound": 0, "invoice": 0, "receipt": 0}

    for so in orders:
        ordered_qty = sum(i.quantity or 0 for i in so.items)
        outbounds = [ob for ob in so.outbounds if ob.status in SHIPPED]
        shipped_qty = sum(ii.quantity or 0 for ob in outbounds for ii in ob.items)

        quote = db.query(Quotation).filter(Quotation.converted_order_id == so.id).first()
        invoices = (
            db.query(Invoice)
            .filter(
                Invoice.direction == "sales",
                Invoice.order_no == so.order_no,
                Invoice.status != "voided",
            )
            .all()
        )
        invoice_amount = round(sum(float(i.total_amount or 0) for i in invoices), 2)
        paid = _customer_paid(db, so.customer_id)
        amount = round(float(so.total_amount or 0), 2)

        # 逐环节判定
        quote_stage = (
            _stage("quote", "报价", "done", quote.quote_no)
            if quote
            else _stage("quote", "报价", "none", "无来源报价（手工建单）")
        )

        order_done = so.status in ORDER_DONE
        order_stage = (
            _stage("order", "订单", "done", "已确认/完成")
            if order_done
            else _stage("order", "订单", "pending", f"订单状态：{so.status}")
        )

        if shipped_qty >= ordered_qty and ordered_qty > 0:
            outbound_stage = _stage("outbound", "出库", "done", f"已出库 {shipped_qty}/{ordered_qty}")
        elif shipped_qty > 0:
            outbound_stage = _stage("outbound", "出库", "partial", f"部分出库 {shipped_qty}/{ordered_qty}")
        else:
            outbound_stage = _stage("outbound", "出库", "pending", "未出库")

        if invoice_amount >= amount and amount > 0:
            invoice_stage = _stage("invoice", "开票", "done", f"已开票 {invoice_amount:,.0f}")
        elif invoice_amount > 0:
            invoice_stage = _stage("invoice", "开票", "partial", f"部分开票 {invoice_amount:,.0f}/{amount:,.0f}")
        else:
            invoice_stage = _stage("invoice", "开票", "pending", "未开票")

        if paid >= amount and amount > 0:
            receipt_stage = _stage("receipt", "收款", "done", f"已收款 {paid:,.0f}")
        elif paid > 0:
            receipt_stage = _stage("receipt", "收款", "partial", f"部分收款 {paid:,.0f}/{amount:,.0f}")
        else:
            receipt_stage = _stage("receipt", "收款", "pending", "未收款")

        stages = [quote_stage, order_stage, outbound_stage, invoice_stage, receipt_stage]
        # 瓶颈：第一个未完成的关键环节（订单 / 出库 / 开票 / 收款）
        bottleneck = ""
        for s in stages:
            if s["key"] != "quote" and s["status"] != "done":
                bottleneck = s["label"]
                stuck[s["key"]] += 1
                break

        rows.append(
            {
                "order_id": so.id,
                "order_no": so.order_no,
                "customer": so.customer.name if so.customer else "",
                "order_amount": amount,
                "order_status": so.status,
                "quote_no": quote.quote_no if quote else "",
                "ordered_qty": ordered_qty,
                "shipped_qty": shipped_qty,
                "invoice_amount": invoice_amount,
                "paid_amount": paid,
                "stages": stages,
                "bottleneck": bottleneck,
            }
        )

    return {
        "total": len(rows),
        "stuck": stuck,
        "items": rows,
    }