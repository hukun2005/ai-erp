"""应付与供应商对账服务，口径与应收完全对称：

应付 = 已入库采购单金额 − 已确认采购退货 − 已付款核销；
付款单可作废，作废后自动回退应付；
供应商对账单同样是某一期台账的冻结快照。
"""
import json
from datetime import datetime, timedelta
from hashlib import sha256

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    OpeningBalance,
    PurchaseOrder,
    PurchaseReturn,
    Supplier,
    SupplierPayment,
    SupplierPaymentAllocation,
    SupplierStatement,
)

AP_STATUS = ("received",)  # 形成应付的采购单状态


def allocated_by_supplier(db: Session) -> dict[int, float]:
    rows = db.execute(
        select(PurchaseOrder.supplier_id, func.sum(SupplierPaymentAllocation.amount))
        .join(SupplierPaymentAllocation, SupplierPaymentAllocation.order_id == PurchaseOrder.id)
        .join(SupplierPayment, SupplierPayment.id == SupplierPaymentAllocation.payment_id)
        .where(SupplierPayment.status == "posted")
        .group_by(PurchaseOrder.supplier_id)
    ).all()
    return {int(sid): round(float(amount or 0), 2) for sid, amount in rows}


def allocated_by_order(db: Session, supplier_id: int | None = None) -> dict[int, float]:
    stmt = (
        select(SupplierPaymentAllocation.order_id, func.sum(SupplierPaymentAllocation.amount))
        .join(SupplierPayment, SupplierPayment.id == SupplierPaymentAllocation.payment_id)
        .where(SupplierPayment.status == "posted")
        .group_by(SupplierPaymentAllocation.order_id)
    )
    if supplier_id:
        stmt = stmt.join(PurchaseOrder, PurchaseOrder.id == SupplierPaymentAllocation.order_id).where(
            PurchaseOrder.supplier_id == supplier_id
        )
    return {int(oid): round(float(amount or 0), 2) for oid, amount in db.execute(stmt).all()}


def _returned_amount(db: Session) -> dict[int, float]:
    rows = db.execute(
        select(PurchaseReturn.supplier_id, func.sum(PurchaseReturn.total_amount))
        .where(PurchaseReturn.status == "confirmed")
        .group_by(PurchaseReturn.supplier_id)
    ).all()
    return {int(sid): round(float(amount or 0), 2) for sid, amount in rows}


def supplier_payables(db: Session) -> list[dict]:
    """供应商维度应付账龄：只返回还有余额的供应商。"""
    rows = db.execute(
        select(
            Supplier.id,
            Supplier.name,
            Supplier.supply_category,
            func.sum(PurchaseOrder.total_amount).label("total"),
            func.count(PurchaseOrder.id).label("orders"),
            func.min(PurchaseOrder.order_date).label("oldest"),
        )
        .join(PurchaseOrder, PurchaseOrder.supplier_id == Supplier.id)
        .where(PurchaseOrder.status.in_(AP_STATUS))
        .group_by(Supplier.id)
        .order_by(func.sum(PurchaseOrder.total_amount).desc())
    ).all()

    allocated = allocated_by_supplier(db)
    returned = _returned_amount(db)
    opening_rows = db.scalars(
        select(OpeningBalance).where(OpeningBalance.partner_type == "supplier")
    ).all()
    opening_map = {int(row.partner_id): row for row in opening_rows}
    today = datetime.now().date()
    items = []
    for row in rows:
        total = round(float(row.total or 0), 2)
        returned_amount = returned.get(int(row.id), 0.0)
        paid = allocated.get(int(row.id), 0.0)
        balance = round(total - returned_amount - paid, 2)
        opening = opening_map.pop(int(row.id), None)
        oldest = row.oldest.date() if row.oldest else today
        if opening and opening.amount > 0:
            balance = round(balance + float(opening.amount), 2)
            oldest = min(oldest, opening.balance_date.date())
        if balance <= 0.005:
            continue
        age = (today - oldest).days
        items.append(
            {
                "supplier_id": int(row.id),
                "supplier": row.name,
                "category": row.supply_category,
                "total": total,
                "returned": returned_amount,
                "paid": paid,
                "balance": balance,
                "opening": round(float(opening.amount), 2) if opening else 0.0,
                "orders": int(row.orders or 0),
                "age_days": age,
                "bucket": "0-30天" if age <= 30 else ("31-60天" if age <= 60 else "60天以上"),
            }
        )

    # 只有期初欠款、还没发生过采购的供应商，也要出现在账龄里
    for supplier_id, opening in opening_map.items():
        if opening.amount <= 0.005:
            continue
        supplier = db.get(Supplier, supplier_id)
        if not supplier:
            continue
        age = (today - opening.balance_date.date()).days
        items.append(
            {
                "supplier_id": supplier_id,
                "supplier": supplier.name,
                "category": supplier.supply_category,
                "total": 0.0,
                "returned": 0.0,
                "paid": 0.0,
                "balance": round(float(opening.amount), 2),
                "opening": round(float(opening.amount), 2),
                "orders": 0,
                "age_days": age,
                "bucket": "0-30天" if age <= 30 else ("31-60天" if age <= 60 else "60天以上"),
            }
        )
    items.sort(key=lambda item: item["balance"], reverse=True)
    return items


def payable_total(db: Session) -> float:
    total = db.scalar(
        select(func.coalesce(func.sum(PurchaseOrder.total_amount), 0)).where(
            PurchaseOrder.status.in_(AP_STATUS)
        )
    ) or 0
    returned = sum(_returned_amount(db).values())
    paid = db.scalar(
        select(func.coalesce(func.sum(SupplierPayment.amount), 0)).where(
            SupplierPayment.status == "posted"
        )
    ) or 0
    opening = db.scalar(
        select(func.coalesce(func.sum(OpeningBalance.amount), 0)).where(
            OpeningBalance.partner_type == "supplier"
        )
    ) or 0
    return round(float(total) - float(returned) - float(paid) + float(opening), 2)


def open_orders(db: Session, supplier_id: int) -> list[dict]:
    """供应商未结清入库单，按时间正序，供付款核销。"""
    allocated = allocated_by_order(db, supplier_id)
    returned = {
        int(oid): round(float(amount or 0), 2)
        for oid, amount in db.execute(
            select(PurchaseReturn.order_id, func.sum(PurchaseReturn.total_amount))
            .where(PurchaseReturn.supplier_id == supplier_id)
            .where(PurchaseReturn.status == "confirmed")
            .group_by(PurchaseReturn.order_id)
        ).all()
    }
    rows = db.scalars(
        select(PurchaseOrder)
        .where(PurchaseOrder.supplier_id == supplier_id)
        .where(PurchaseOrder.status.in_(AP_STATUS))
        .order_by(PurchaseOrder.order_date, PurchaseOrder.id)
    ).all()
    items = []
    for order in rows:
        paid = allocated.get(order.id, 0.0)
        returned_amount = returned.get(order.id, 0.0)
        balance = round(float(order.total_amount) - returned_amount - paid, 2)
        if balance <= 0.005:
            continue
        items.append(
            {
                "order_id": order.id,
                "order_no": order.order_no,
                "order_date": order.order_date.strftime("%Y-%m-%d"),
                "total_amount": round(float(order.total_amount), 2),
                "returned": returned_amount,
                "allocated": paid,
                "balance": balance,
            }
        )
    return items


def allocate(db: Session, payment: SupplierPayment, allocations: list[dict] | None) -> None:
    """把付款核销到入库单；未指定时按先进先出自动分摊。"""
    open_rows = {row["order_id"]: row["balance"] for row in open_orders(db, payment.supplier_id)}
    remaining = round(float(payment.amount), 2)

    if allocations:
        for item in allocations:
            order_id = int(item["order_id"])
            amount = round(float(item["amount"]), 2)
            if order_id not in open_rows:
                raise ValueError(f"采购单 {order_id} 不属于该供应商或已结清")
            if amount > open_rows[order_id] + 0.005:
                raise ValueError(f"核销金额超过采购单 {order_id} 的未结余额 {open_rows[order_id]}")
            if amount > remaining + 0.005:
                raise ValueError("核销金额合计超过付款金额")
            payment.allocations.append(
                SupplierPaymentAllocation(order_id=order_id, amount=amount)
            )
            remaining = round(remaining - amount, 2)
        return

    for order_id, balance in open_rows.items():
        if remaining <= 0.005:
            break
        take = min(balance, remaining)
        payment.allocations.append(
            SupplierPaymentAllocation(order_id=order_id, amount=round(take, 2))
        )
        remaining = round(remaining - take, 2)


# ---------------- 对账单 ----------------
def _canonical(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_statement(db: Session, supplier_id: int, period_start: str, period_end: str) -> dict:
    supplier = db.get(Supplier, supplier_id)
    if not supplier:
        raise ValueError("供应商不存在")
    start = datetime.fromisoformat(period_start)
    end_exclusive = datetime.fromisoformat(period_end) + timedelta(days=1)
    if end_exclusive <= start:
        raise ValueError("账期结束日期必须晚于开始日期")

    orders_before = db.scalar(
        select(func.coalesce(func.sum(PurchaseOrder.total_amount), 0))
        .where(PurchaseOrder.supplier_id == supplier_id)
        .where(PurchaseOrder.status.in_(AP_STATUS))
        .where(PurchaseOrder.order_date < start)
    ) or 0
    returns_before = db.scalar(
        select(func.coalesce(func.sum(PurchaseReturn.total_amount), 0))
        .where(PurchaseReturn.supplier_id == supplier_id)
        .where(PurchaseReturn.status == "confirmed")
        .where(PurchaseReturn.confirmed_at < start)
    ) or 0
    paid_before = db.scalar(
        select(func.coalesce(func.sum(SupplierPayment.amount), 0))
        .where(SupplierPayment.supplier_id == supplier_id)
        .where(SupplierPayment.status == "posted")
        .where(SupplierPayment.pay_date < start)
    ) or 0
    opening_record = db.scalar(
        select(OpeningBalance)
        .where(OpeningBalance.partner_type == "supplier")
        .where(OpeningBalance.partner_id == supplier_id)
    )
    opening_extra = 0.0
    opening_entries: list[dict] = []
    if opening_record and opening_record.amount > 0:
        if opening_record.balance_date < start:
            opening_extra = round(float(opening_record.amount), 2)
        elif opening_record.balance_date < end_exclusive:
            opening_entries.append(
                {
                    "date": opening_record.balance_date.strftime("%Y-%m-%d"),
                    "sort": opening_record.balance_date,
                    "type": "opening",
                    "no": "期初",
                    "summary": "期初应付余额",
                    "increase": round(float(opening_record.amount), 2),
                    "decrease": 0.0,
                    "ref_id": opening_record.id,
                }
            )
    opening = round(
        float(orders_before) - float(returns_before) - float(paid_before) + opening_extra, 2
    )

    orders = db.scalars(
        select(PurchaseOrder)
        .where(PurchaseOrder.supplier_id == supplier_id)
        .where(PurchaseOrder.status.in_(AP_STATUS))
        .where(PurchaseOrder.order_date >= start)
        .where(PurchaseOrder.order_date < end_exclusive)
        .order_by(PurchaseOrder.order_date, PurchaseOrder.id)
    ).all()
    returns = db.scalars(
        select(PurchaseReturn)
        .where(PurchaseReturn.supplier_id == supplier_id)
        .where(PurchaseReturn.status == "confirmed")
        .where(PurchaseReturn.confirmed_at >= start)
        .where(PurchaseReturn.confirmed_at < end_exclusive)
        .order_by(PurchaseReturn.confirmed_at, PurchaseReturn.id)
    ).all()
    payments = db.scalars(
        select(SupplierPayment)
        .where(SupplierPayment.supplier_id == supplier_id)
        .where(SupplierPayment.status == "posted")
        .where(SupplierPayment.pay_date >= start)
        .where(SupplierPayment.pay_date < end_exclusive)
        .order_by(SupplierPayment.pay_date, SupplierPayment.id)
    ).all()

    entries: list[dict] = []
    for order in orders:
        entries.append(
            {
                "date": order.order_date.strftime("%Y-%m-%d"),
                "sort": order.order_date,
                "type": "order",
                "no": order.order_no,
                "summary": "采购入库",
                "increase": round(float(order.total_amount), 2),
                "decrease": 0.0,
                "ref_id": order.id,
            }
        )
    for ret in returns:
        entries.append(
            {
                "date": ret.confirmed_at.strftime("%Y-%m-%d"),
                "sort": ret.confirmed_at,
                "type": "return",
                "no": ret.return_no,
                "summary": f"采购退货（原单 {ret.order.order_no if ret.order else ''}）",
                "increase": 0.0,
                "decrease": round(float(ret.total_amount), 2),
                "ref_id": ret.id,
            }
        )
    for payment in payments:
        method = payment.method + (f" {payment.reference}" if payment.reference else "")
        entries.append(
            {
                "date": payment.pay_date.strftime("%Y-%m-%d"),
                "sort": payment.pay_date,
                "type": "payment",
                "no": payment.payment_no,
                "summary": f"付款（{method}）",
                "increase": 0.0,
                "decrease": round(float(payment.amount), 2),
                "ref_id": payment.id,
            }
        )
    entries.extend(opening_entries)
    entries.sort(key=lambda item: (item["sort"], item["type"]))

    balance = opening
    lines = []
    for entry in entries:
        balance = round(balance + entry["increase"] - entry["decrease"], 2)
        lines.append(
            {
                "date": entry["date"],
                "type": entry["type"],
                "no": entry["no"],
                "summary": entry["summary"],
                "increase": entry["increase"],
                "decrease": entry["decrease"],
                "balance": balance,
                "ref_id": entry["ref_id"],
            }
        )

    increase_sum = round(sum(line["increase"] for line in lines), 2)
    decrease_sum = round(sum(line["decrease"] for line in lines), 2)
    return {
        "supplier": {
            "id": supplier.id,
            "name": supplier.name,
            "contact": supplier.contact,
            "phone": supplier.phone,
            "category": supplier.supply_category,
        },
        "period": {"start": period_start, "end": period_end},
        "opening": opening,
        "increase": increase_sum,
        "decrease": decrease_sum,
        "closing": round(opening + increase_sum - decrease_sum, 2),
        "lines": lines,
    }


def freeze_statement(
    db: Session, supplier_id: int, period_start: str, period_end: str, created_by: str
) -> SupplierStatement:
    payload = build_statement(db, supplier_id, period_start, period_end)
    previous = db.scalar(
        select(func.count())
        .select_from(SupplierStatement)
        .where(SupplierStatement.supplier_id == supplier_id)
        .where(SupplierStatement.period_start == period_start)
        .where(SupplierStatement.period_end == period_end)
    ) or 0
    version = int(previous) + 1
    period_tag = period_end.replace("-", "")
    statement = SupplierStatement(
        statement_no=f"SDC{period_tag}-{supplier_id:04d}-V{version}",
        supplier_id=supplier_id,
        period_start=period_start,
        period_end=period_end,
        version=version,
        opening_amount=payload["opening"],
        increase_amount=payload["increase"],
        decrease_amount=payload["decrease"],
        closing_amount=payload["closing"],
        payload=json.dumps(payload, ensure_ascii=False),
        content_hash=sha256(_canonical(payload).encode("utf-8")).hexdigest(),
        created_by=created_by,
    )
    db.add(statement)
    db.flush()
    return statement


def statement_payload(statement: SupplierStatement) -> dict:
    return json.loads(statement.payload or "{}")
