"""银行流水导入与自动对账。

导入网银导出的流水文件（.xlsx），逐条登记 BankStatement；
对每一条流水尝试与应收（客户回款）或应付（供应商付款）核销，
生成 BankStatementMatch 建议，用户确认后落库生成收/付款单并完成核销。

匹配复用 services.reconciliation.match（单号/金额/组合/先进先出同一套策略）。
"""
import json
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    BankStatement,
    BankStatementMatch,
    Customer,
    Payment,
    Supplier,
    SupplierPayment,
)
from . import payables, receivables, reconciliation


def _next_rc_no(db: Session) -> str:
    today = datetime.now().strftime("%Y%m%d")
    count = db.scalar(
        select(func.count()).select_from(Payment).where(Payment.payment_no.like(f"RC{today}%"))
    ) or 0
    return f"RC{today}{int(count) + 1:04d}"


def _next_py_no(db: Session) -> str:
    today = datetime.now().strftime("%Y%m%d")
    count = db.scalar(
        select(func.count()).select_from(SupplierPayment).where(SupplierPayment.payment_no.like(f"PY{today}%"))
    ) or 0
    return f"PY{today}{int(count) + 1:04d}"


def _norm(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def find_partner(db: Session, direction: str, counterparty: str) -> tuple[str, int, str] | None:
    """按对方户名在客户/供应商里找往来对象；流入找客户，流出找供应商。"""
    text = _norm(counterparty)
    if not text:
        return None
    model = Customer if direction == "in" else Supplier
    kind = "customer" if direction == "in" else "supplier"
    for row in db.query(model).all():
        if _norm(row.name) == text:
            return kind, row.id, row.name
    # 退一步：包含关系
    for row in db.query(model).all():
        name = _norm(row.name)
        if name and (name in text or text in name):
            return kind, row.id, row.name
    return None


def _suggest(db: Session, statement: BankStatement) -> dict | None:
    """为一条流水生成核销建议；找不到往来对象或没有未结单据时返回 None。"""
    partner = find_partner(db, statement.direction, statement.counterparty)
    if partner is None:
        return None
    kind, partner_id, partner_name = partner
    open_rows = (
        receivables.open_orders(db, partner_id)
        if kind == "customer"
        else payables.open_orders(db, partner_id)
    )
    result = reconciliation.match(
        open_rows,
        statement.amount,
        pay_date=statement.trans_date,
        reference=statement.ref_no or statement.summary,
    )
    if result["strategy"] == "none" or result["confidence"] == "none":
        return None
    return {
        "partner_type": kind,
        "partner_id": partner_id,
        "partner_name": partner_name,
        "amount": statement.amount,
        "confidence": result["confidence"],
        "strategy": result["strategy"],
        "allocations": result["allocations"],
    }


def import_statements(db: Session, rows: list[dict], username: str) -> dict:
    """rows: [{trans_date, direction, amount, counterparty, ref_no, summary, bank_account}]"""
    created = 0
    skipped = 0
    errors: list[dict] = []
    for index, row in enumerate(rows, start=1):
        try:
            trans_date = row.get("trans_date") or datetime.now()
            amount = round(float(row.get("amount") or 0), 2)
            if amount <= 0:
                raise ValueError("金额必须大于 0")
            ref_no = str(row.get("ref_no") or "").strip()
            direction = "in" if str(row.get("direction") or "").strip() in ("收入", "流入", "in", "借") else "out"
            # 去重：同一流水号 + 日期 + 金额
            dup = db.scalar(
                select(BankStatement.id).where(
                    BankStatement.ref_no == ref_no,
                    BankStatement.trans_date == trans_date,
                    BankStatement.amount == amount,
                ).limit(1)
            )
            if dup:
                skipped += 1
                continue
            statement = BankStatement(
                bank_account=str(row.get("bank_account") or "").strip(),
                ref_no=ref_no,
                trans_date=trans_date,
                direction=direction,
                amount=amount,
                counterparty=str(row.get("counterparty") or "").strip(),
                summary=str(row.get("summary") or "").strip(),
                status="unmatched",
                created_by=username,
            )
            db.add(statement)
            db.flush()
            created += 1

            suggestion = _suggest(db, statement)
            if suggestion:
                db.add(
                    BankStatementMatch(
                        statement_id=statement.id,
                        partner_type=suggestion["partner_type"],
                        partner_id=suggestion["partner_id"],
                        partner_name=suggestion["partner_name"],
                        amount=suggestion["amount"],
                        confidence=suggestion["confidence"],
                        strategy=suggestion["strategy"],
                        allocations=json.dumps(suggestion["allocations"], ensure_ascii=False),
                        status="suggested",
                    )
                )
        except ValueError as exc:
            errors.append({"row": index, "reason": str(exc)})
    db.commit()
    return {"created": created, "skipped": skipped, "errors": errors}


def apply_match(db: Session, match_id: int, username: str) -> dict:
    """确认一条对账建议：生成收/付款单并核销，标记流水已匹配。"""
    match = db.get(BankStatementMatch, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="对账建议不存在")
    if match.status == "applied":
        raise HTTPException(status_code=400, detail="该建议已处理")
    statement = db.get(BankStatement, match.statement_id)
    if not statement or statement.status != "unmatched":
        raise HTTPException(status_code=400, detail="该流水已处理")
    allocations = json.loads(match.allocations or "[]")

    if match.partner_type == "customer":
        payment = Payment(
            payment_no=_next_rc_no(db),
            customer_id=match.partner_id,
            amount=round(float(match.amount), 2),
            pay_date=statement.trans_date,
            method="银行流水导入",
            reference=statement.ref_no or statement.summary,
            remark=f"银行流水对账：{statement.summary}".strip(),
            created_by=username,
        )
        try:
            receivables.allocate(db, payment, allocations)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.add(payment)
        applied_id = payment.id
        applied_no = payment.payment_no
    else:
        payment = SupplierPayment(
            payment_no=_next_py_no(db),
            supplier_id=match.partner_id,
            amount=round(float(match.amount), 2),
            pay_date=statement.trans_date,
            method="银行流水导入",
            reference=statement.ref_no or statement.summary,
            remark=f"银行流水对账：{statement.summary}".strip(),
            created_by=username,
        )
        try:
            payables.allocate(db, payment, allocations)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.add(payment)
        applied_id = payment.id
        applied_no = payment.payment_no

    match.status = "applied"
    match.applied_id = applied_id
    statement.status = "matched"
    statement.matched_type = "receipt" if match.partner_type == "customer" else "payment"
    statement.matched_id = applied_id
    db.commit()
    return {"match_id": match.id, "payment_no": applied_no, "partner_name": match.partner_name}


def dismiss_match(db: Session, match_id: int) -> dict:
    """忽略一条建议并标记流水为 ignored（费用、手续费等不入账）。"""
    match = db.get(BankStatementMatch, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="对账建议不存在")
    if match.status == "applied":
        raise HTTPException(status_code=400, detail="该建议已处理")
    match.status = "dismissed"
    statement = db.get(BankStatement, match.statement_id)
    if statement and statement.status == "unmatched":
        statement.status = "ignored"
    db.commit()
    return {"ok": True}
