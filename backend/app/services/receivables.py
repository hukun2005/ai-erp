"""应收与对账服务。

三个概念：
- 应收 = 已确认/已完成订单金额 − 已核销金额（收款作废自动回退）；
- 台账 = 订单（增加）与收款（减少）按时间排序，逐行带余额；
- 对账单 = 某一期台账的快照，生成即冻结，之后改单不影响已发出的版本。

没有收款单时，「应收」只能按订单一侧计账，对方一句「我早付了」就对不下去，
所以核销是这一整套的地基。
"""
import json
import secrets
from datetime import datetime, timedelta
from hashlib import sha256

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Customer,
    DocumentEvent,
    OpeningBalance,
    Payment,
    PaymentAllocation,
    SalesOrder,
    SalesReturn,
    ShareToken,
    StatementSnapshot,
    VerificationCode,
)
from . import sms
from .returns import sales_returned_amount_by_customer, sales_returned_amount_by_order

AR_STATUS = ("confirmed", "completed")  # 形成应收的订单状态
TOKEN_TTL_DAYS = 7
TOKEN_MAX_USES = 20


# ---------------- 应收 ----------------
def allocated_by_customer(db: Session) -> dict[int, float]:
    """客户维度已核销金额；作废收款不计入。"""
    rows = db.execute(
        select(SalesOrder.customer_id, func.sum(PaymentAllocation.amount))
        .join(PaymentAllocation, PaymentAllocation.order_id == SalesOrder.id)
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .where(Payment.status == "posted")
        .group_by(SalesOrder.customer_id)
    ).all()
    return {int(cid): round(float(amount or 0), 2) for cid, amount in rows}


def allocated_by_order(db: Session, customer_id: int | None = None) -> dict[int, float]:
    """订单维度已核销金额，用于计算单张订单的未结余额。"""
    stmt = (
        select(PaymentAllocation.order_id, func.sum(PaymentAllocation.amount))
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .where(Payment.status == "posted")
        .group_by(PaymentAllocation.order_id)
    )
    if customer_id:
        stmt = stmt.join(SalesOrder, SalesOrder.id == PaymentAllocation.order_id).where(
            SalesOrder.customer_id == customer_id
        )
    return {int(oid): round(float(amount or 0), 2) for oid, amount in db.execute(stmt).all()}


def customer_receivables(db: Session) -> list[dict]:
    """客户维度应收账龄：只返回还有余额的客户。"""
    rows = db.execute(
        select(
            Customer.id,
            Customer.name,
            Customer.level,
            func.sum(SalesOrder.total_amount).label("total"),
            func.count(SalesOrder.id).label("orders"),
            func.min(SalesOrder.order_date).label("oldest"),
        )
        .join(SalesOrder, SalesOrder.customer_id == Customer.id)
        .where(SalesOrder.status.in_(AR_STATUS))
        .group_by(Customer.id)
        .order_by(func.sum(SalesOrder.total_amount).desc())
    ).all()

    allocated = allocated_by_customer(db)
    returned = sales_returned_amount_by_customer(db)
    opening_rows = db.scalars(
        select(OpeningBalance).where(OpeningBalance.partner_type == "customer")
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
                "customer_id": int(row.id),
                "customer": row.name,
                "level": row.level,
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

    # 只有期初欠款、还没下过单的客户，也要出现在账龄里
    for customer_id, opening in opening_map.items():
        if opening.amount <= 0.005:
            continue
        customer = db.get(Customer, customer_id)
        if not customer:
            continue
        age = (today - opening.balance_date.date()).days
        items.append(
            {
                "customer_id": customer_id,
                "customer": customer.name,
                "level": customer.level,
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


def receivable_total(db: Session) -> float:
    """全部应收余额 = 已确认订单 − 已确认退货 − 已收款 + 期初应收。"""
    orders = db.scalar(
        select(func.coalesce(func.sum(SalesOrder.total_amount), 0)).where(
            SalesOrder.status.in_(AR_STATUS)
        )
    ) or 0
    returned = sum(sales_returned_amount_by_customer(db).values())
    paid = db.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "posted")
    ) or 0
    opening = db.scalar(
        select(func.coalesce(func.sum(OpeningBalance.amount), 0)).where(
            OpeningBalance.partner_type == "customer"
        )
    ) or 0
    return round(float(orders) - float(returned) - float(paid) + float(opening), 2)


def open_orders(db: Session, customer_id: int) -> list[dict]:
    """客户未结清订单，按时间正序，供核销时按先进先出手动选择。"""
    allocated = allocated_by_order(db, customer_id)
    returned = sales_returned_amount_by_order(db)
    rows = db.scalars(
        select(SalesOrder)
        .where(SalesOrder.customer_id == customer_id)
        .where(SalesOrder.status.in_(AR_STATUS))
        .order_by(SalesOrder.order_date, SalesOrder.id)
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


# ---------------- 核销 ----------------
def allocate(db: Session, payment: Payment, allocations: list[dict] | None) -> None:
    """把收款核销到订单。

    未指定明细时按先进先出自动分摊；指定时逐条校验金额不超过订单余额。
    """
    open_rows = {row["order_id"]: row["balance"] for row in open_orders(db, payment.customer_id)}
    remaining = round(float(payment.amount), 2)

    if allocations:
        for item in allocations:
            order_id = int(item["order_id"])
            amount = round(float(item["amount"]), 2)
            if order_id not in open_rows:
                raise ValueError(f"订单 {order_id} 不属于该客户或已结清")
            if amount > open_rows[order_id] + 0.005:
                raise ValueError(f"核销金额超过订单 {order_id} 的未结余额 {open_rows[order_id]}")
            if amount > remaining + 0.005:
                raise ValueError("核销金额合计超过收款金额")
            payment.allocations.append(PaymentAllocation(order_id=order_id, amount=amount))
            remaining = round(remaining - amount, 2)
        return

    for order_id, balance in open_rows.items():
        if remaining <= 0.005:
            break
        take = min(balance, remaining)
        payment.allocations.append(
            PaymentAllocation(order_id=order_id, amount=round(take, 2))
        )
        remaining = round(remaining - take, 2)


# ---------------- 对账单 ----------------
def _canonical(payload: dict) -> str:
    """稳定序列化，保证同样的内容永远得到同样的哈希。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_statement(db: Session, customer_id: int, period_start: str, period_end: str) -> dict:
    """按客户与账期生成台账：期初 / 本期增加 / 本期减少 / 期末。"""
    customer = db.get(Customer, customer_id)
    if not customer:
        raise ValueError("客户不存在")
    start = datetime.fromisoformat(period_start)
    end_exclusive = datetime.fromisoformat(period_end) + timedelta(days=1)
    if end_exclusive <= start:
        raise ValueError("账期结束日期必须晚于开始日期")

    orders_before = db.scalar(
        select(func.coalesce(func.sum(SalesOrder.total_amount), 0))
        .where(SalesOrder.customer_id == customer_id)
        .where(SalesOrder.status.in_(AR_STATUS))
        .where(SalesOrder.order_date < start)
    ) or 0
    paid_before = db.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.customer_id == customer_id)
        .where(Payment.status == "posted")
        .where(Payment.pay_date < start)
    ) or 0
    returns_before = db.scalar(
        select(func.coalesce(func.sum(SalesReturn.total_amount), 0))
        .where(SalesReturn.customer_id == customer_id)
        .where(SalesReturn.status == "confirmed")
        .where(SalesReturn.confirmed_at < start)
    ) or 0
    opening_record = db.scalar(
        select(OpeningBalance)
        .where(OpeningBalance.partner_type == "customer")
        .where(OpeningBalance.partner_id == customer_id)
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
                    "summary": "期初应收余额",
                    "increase": round(float(opening_record.amount), 2),
                    "decrease": 0.0,
                    "ref_id": opening_record.id,
                }
            )
    opening = round(
        float(orders_before) - float(returns_before) - float(paid_before) + opening_extra, 2
    )

    orders = db.scalars(
        select(SalesOrder)
        .where(SalesOrder.customer_id == customer_id)
        .where(SalesOrder.status.in_(AR_STATUS))
        .where(SalesOrder.order_date >= start)
        .where(SalesOrder.order_date < end_exclusive)
        .order_by(SalesOrder.order_date, SalesOrder.id)
    ).all()
    returns = db.scalars(
        select(SalesReturn)
        .where(SalesReturn.customer_id == customer_id)
        .where(SalesReturn.status == "confirmed")
        .where(SalesReturn.confirmed_at >= start)
        .where(SalesReturn.confirmed_at < end_exclusive)
        .order_by(SalesReturn.confirmed_at, SalesReturn.id)
    ).all()
    payments = db.scalars(
        select(Payment)
        .where(Payment.customer_id == customer_id)
        .where(Payment.status == "posted")
        .where(Payment.pay_date >= start)
        .where(Payment.pay_date < end_exclusive)
        .order_by(Payment.pay_date, Payment.id)
    ).all()

    entries: list[dict] = []
    for order in orders:
        entries.append(
            {
                "date": order.order_date.strftime("%Y-%m-%d"),
                "sort": order.order_date,
                "type": "order",
                "no": order.order_no,
                "summary": f"销售出库（{order.status}）",
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
                "summary": f"销售退货（原单 {ret.order.order_no if ret.order else ''}）",
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
                "summary": f"收款（{method}）",
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
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "level": customer.level,
            "contact": customer.contact,
            "phone": customer.phone,
        },
        "period": {"start": period_start, "end": period_end},
        "opening": opening,
        "increase": increase_sum,
        "decrease": decrease_sum,
        "closing": round(opening + increase_sum - decrease_sum, 2),
        "lines": lines,
    }


def freeze_statement(db: Session, customer_id: int, period_start: str, period_end: str, created_by: str) -> StatementSnapshot:
    """把台账固化成快照：内容、哈希、版本一次写死。"""
    payload = build_statement(db, customer_id, period_start, period_end)
    previous = db.scalar(
        select(func.count())
        .select_from(StatementSnapshot)
        .where(StatementSnapshot.customer_id == customer_id)
        .where(StatementSnapshot.period_start == period_start)
        .where(StatementSnapshot.period_end == period_end)
    ) or 0
    version = int(previous) + 1
    period_tag = period_end.replace("-", "")
    statement = StatementSnapshot(
        statement_no=f"DC{period_tag}-{customer_id:04d}-V{version}",
        customer_id=customer_id,
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


def statement_payload(statement: StatementSnapshot) -> dict:
    """读取冻结内容；这是唯一可信来源，公开页面也只渲染它。"""
    return json.loads(statement.payload or "{}")


# ---------------- 分享与留痕 ----------------
def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def issue_share_token(
    db: Session, statement: StatementSnapshot, phone: str = "", days: int = TOKEN_TTL_DAYS
) -> tuple[str, str]:
    """签发分享凭证，返回明文（链接 token、确认口令）；库里只存哈希。"""
    token = secrets.token_urlsafe(24)
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(
        ShareToken(
            statement_id=statement.id,
            token_hash=_hash(token),
            code_hash=_hash(code),
            phone=phone,
            expires_at=datetime.now() + timedelta(days=days),
            max_uses=TOKEN_MAX_USES,
        )
    )
    db.flush()
    return token, code


def resolve_share(db: Session, token: str) -> tuple[ShareToken | None, str]:
    """只校验链接本身（存在 / 未撤销 / 未过期 / 次数未用尽），不校验验证码。"""
    record = db.scalar(select(ShareToken).where(ShareToken.token_hash == _hash(token)))
    if not record:
        return None, "链接无效或已失效"
    if record.revoked_at:
        return None, "链接已被撤销"
    if record.expires_at < datetime.now():
        return None, "链接已过期，请联系业务员重新发送"
    if record.used_count >= record.max_uses:
        return None, "该链接查看次数已达上限"
    return record, ""


def verify_share(
    db: Session, token: str, code: str, consume: bool = False
) -> tuple[ShareToken | None, str]:
    """校验链接与验证码，返回 (凭证, 失败原因)。

    配置了短信时校验短信码（确认动作才消费）；否则校验业务员单独下发的固定口令。
    """
    record, error = resolve_share(db, token)
    if not record:
        return None, error

    if settings.sms_enabled:
        if not check_sms_code(db, record, code, consume=consume):
            return None, "验证码不正确或已过期"
        return record, ""

    if not code or _hash(code) != record.code_hash:
        return None, "确认口令不正确"
    return record, ""


def issue_sms_code(db: Session, share: ShareToken, phone: str) -> tuple[str, str]:
    """生成并下发短信验证码，返回 (验证码, 发送说明)。

    旧码立即作废，避免一条链接上同时存在多个可用码。
    """
    for stale in db.scalars(
        select(VerificationCode)
        .where(VerificationCode.token_hash == share.token_hash)
        .where(VerificationCode.used == False)  # noqa: E712
    ).all():
        stale.used = True

    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(
        VerificationCode(
            statement_id=share.statement_id,
            token_hash=share.token_hash,
            phone=phone,
            code_hash=_hash(code),
            expires_at=datetime.now() + timedelta(minutes=settings.SMS_CODE_TTL_MINUTES),
        )
    )
    db.flush()
    ok, message = sms.send_code(phone, code)
    return (code if ok else ""), message


def check_sms_code(db: Session, share: ShareToken, code: str, consume: bool = True) -> bool:
    """校验短信验证码；consume=True 时消费掉，防止一码多用。"""
    record = db.scalar(
        select(VerificationCode)
        .where(VerificationCode.token_hash == share.token_hash)
        .where(VerificationCode.used == False)  # noqa: E712
        .order_by(VerificationCode.id.desc())
    )
    if not record or record.expires_at < datetime.now():
        return False
    if record.attempts >= settings.SMS_MAX_ATTEMPTS:
        record.used = True
        db.flush()
        return False
    if not code or _hash(code) != record.code_hash:
        record.attempts += 1
        db.flush()
        return False
    if consume:
        record.used = True
        db.flush()
    return True


def record_event(
    db: Session,
    statement_id: int | None,
    event_type: str,
    *,
    phone: str = "",
    code_verified: bool = False,
    ip: str = "",
    user_agent: str = "",
    detail: str = "",
) -> None:
    """追加一条行为留痕；这是对账能被举证的关键。"""
    db.add(
        DocumentEvent(
            statement_id=statement_id,
            event_type=event_type,
            phone=phone,
            code_verified=code_verified,
            ip=ip[:50],
            user_agent=user_agent[:200],
            detail=detail,
        )
    )
