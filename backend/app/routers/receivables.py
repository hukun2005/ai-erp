"""应收、收款与对账单接口。

对账链路：录收款 → 核销到订单 → 生成对账单快照 → 把链接和口令发给对方确认。
"""
import io
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import require_perm
from ..models import Customer, DocumentEvent, Payment, SalesOrder, StatementSnapshot, User
from ..schemas import PaymentCreate, PaymentMatchRequest, StatementCreate
from ..services import approval_flow, approvals, receivables, reconciliation
from ..services.importer import XLSX_MEDIA_TYPE

router = APIRouter(prefix="/api", tags=["应收与对账"])


def _next_payment_no(db: Session) -> str:
    today = datetime.now().strftime("%Y%m%d")
    count = db.scalar(
        select(func.count()).select_from(Payment).where(Payment.payment_no.like(f"RC{today}%"))
    ) or 0
    return f"RC{today}{int(count) + 1:04d}"


def _payment_payload(payment: Payment) -> dict:
    return {
        "id": payment.id,
        "payment_no": payment.payment_no,
        "customer_id": payment.customer_id,
        "customer_name": payment.customer.name if payment.customer else "",
        "amount": round(float(payment.amount), 2),
        "pay_date": payment.pay_date,
        "method": payment.method,
        "reference": payment.reference,
        "remark": payment.remark,
        "status": payment.status,
        "approval_required": int(payment.approval_required or 0),
        "approval_approved": int(payment.approval_approved or 0),
        "created_by": payment.created_by,
        "allocations": [
            {"order_id": item.order_id, "amount": round(float(item.amount), 2)}
            for item in payment.allocations
        ],
    }


# ---------------- 应收 ----------------
@router.get("/receivables", summary="客户应收账龄", dependencies=[Depends(require_perm("finance:view"))])
def list_receivables(db: Session = Depends(get_db)):
    items = receivables.customer_receivables(db)
    buckets = {"0-30天": 0.0, "31-60天": 0.0, "60天以上": 0.0}
    for item in items:
        buckets[item["bucket"]] = round(buckets[item["bucket"]] + item["balance"], 2)
    return {
        "total": receivables.receivable_total(db),
        "buckets": [{"name": key, "value": value} for key, value in buckets.items()],
        "items": items,
    }


@router.get(
    "/receivables/{customer_id}/orders",
    summary="客户未结清订单（核销候选）",
    dependencies=[Depends(require_perm("finance:view"))],
)
def customer_open_orders(customer_id: int, db: Session = Depends(get_db)):
    return receivables.open_orders(db, customer_id)


@router.post(
    "/payments/match",
    summary="智能核销建议（只算不写）",
    dependencies=[Depends(require_perm("finance:manage"))],
)
def match_payment(payload: PaymentMatchRequest, db: Session = Depends(get_db)):
    """按流水号、金额与账龄给出核销建议；确认后仍走 POST /payments 落库。"""
    customer = db.get(Customer, payload.customer_id)
    if not customer:
        raise HTTPException(status_code=400, detail="客户不存在")
    result = reconciliation.match(
        receivables.open_orders(db, payload.customer_id),
        payload.amount,
        payload.pay_date,
        payload.reference,
    )
    result["party"] = {"id": customer.id, "name": customer.name}
    return result



@router.get("/payments", summary="收款列表", dependencies=[Depends(require_perm("finance:view"))])
def list_payments(
    customer_id: int | None = Query(None),
    keyword: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = select(Payment).join(Customer, Customer.id == Payment.customer_id)
    if customer_id:
        stmt = stmt.where(Payment.customer_id == customer_id)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            Payment.payment_no.like(like) | Customer.name.like(like) | Payment.reference.like(like)
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Payment.pay_date.desc(), Payment.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"total": int(total), "items": [_payment_payload(row) for row in rows]}


@router.post("/payments", summary="登记收款并核销", dependencies=[Depends(require_perm("finance:manage"))])
def create_payment(
    payload: PaymentCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("finance:manage")),
):
    customer = db.get(Customer, payload.customer_id)
    if not customer:
        raise HTTPException(status_code=400, detail="客户不存在")

    # 配置了收款审批规则时先挂起，审批通过才转为 posted 并计入应收核销
    pending = approval_flow.has_rules(db, "payment")
    payment = Payment(
        payment_no=_next_payment_no(db),
        customer_id=payload.customer_id,
        amount=round(float(payload.amount), 2),
        pay_date=payload.pay_date or datetime.now(),
        method=payload.method or "银行转账",
        reference=payload.reference or "",
        remark=payload.remark or "",
        status="pending" if pending else "posted",
        created_by=user.username,
    )
    try:
        receivables.allocate(db, payment, [item.model_dump() for item in payload.allocations])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.add(payment)
    db.flush()
    if pending:
        payment.approval_required = approval_flow.required_levels(
            db, "payment", payment.amount, approval_flow.context_for(db, "payment", payment)
        )
        payment.approval_approved = 0
        approvals.log_action(db, "payment", payment.id, "submit", user)
    db.commit()
    db.refresh(payment)
    record(
        username=user.username,
        role=user.role,
        action="登记收款",
        resource=f"收款单 {payment.payment_no}",
        detail=f"客户 {customer.name}，金额 {payment.amount:,.2f}，核销 {len(payment.allocations)} 单",
        request=request,
    )
    return _payment_payload(payment)


@router.post("/payments/{payment_id}/void", summary="作废收款", dependencies=[Depends(require_perm("finance:manage"))])
def void_payment(
    payment_id: int,
    request: Request,
    reason: str = Query("", max_length=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("finance:manage")),
):
    """作废后核销自动回退，订单余额恢复；不物理删除，保留追溯链。"""
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="收款单不存在")
    if payment.status == "voided":
        raise HTTPException(status_code=400, detail="该收款单已作废")
    payment.status = "voided"
    payment.void_reason = reason or "未填写原因"
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="作废收款",
        resource=f"收款单 {payment.payment_no}",
        detail=payment.void_reason,
        request=request,
    )
    return {"ok": True}


@router.post("/payments/{payment_id}/approve", summary="审批通过收款单（多级审批）")
def approve_payment(
    payment_id: int,
    request: Request,
    comment: str = Query("", description="审批意见"),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("payable:approve")),
):
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="收款单不存在")
    if payment.status != "pending":
        raise HTTPException(status_code=400, detail="该收款单不处于待审批状态")
    result = approval_flow.perform_approval(db, "payment", payment, user, comment)
    if result["completed"]:
        payment.status = "posted"
    db.commit()
    db.refresh(payment)
    record(
        username=user.username,
        role=user.role,
        action="收款单审批通过",
        resource=f"收款单 {payment.payment_no}",
        detail=f"第 {result['level']}/{result['required']} 级" + ("" if result["level_complete"] else "（会签未齐）"),
        request=request,
    )
    payload = _payment_payload(payment)
    payload["pending_next_level"] = result["level_complete"]
    return payload


@router.post("/payments/{payment_id}/reject", summary="审批驳回收款单")
def reject_payment(
    payment_id: int,
    request: Request,
    comment: str = Query("", description="驳回原因"),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("payable:approve")),
):
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="收款单不存在")
    if payment.status != "pending":
        raise HTTPException(status_code=400, detail="该收款单不处于待审批状态")
    level, _rule = approval_flow.ensure_level_permission(db, "payment", payment, user)
    payment.status = "voided"
    payment.void_reason = (comment or "审批驳回")[:200]
    payment.approval_approved = 0
    approvals.log_action(db, "payment", payment.id, "reject", user, comment or "驳回", level=level)
    db.commit()
    db.refresh(payment)
    record(
        username=user.username,
        role=user.role,
        action="收款单审批驳回",
        resource=f"收款单 {payment.payment_no}",
        detail=payment.void_reason,
        request=request,
    )
    return _payment_payload(payment)


# ---------------- 对账单 ----------------
def _statement_payload(statement: StatementSnapshot) -> dict:
    return {
        "id": statement.id,
        "statement_no": statement.statement_no,
        "customer_id": statement.customer_id,
        "customer_name": statement.customer.name if statement.customer else "",
        "period_start": statement.period_start,
        "period_end": statement.period_end,
        "version": statement.version,
        "opening_amount": statement.opening_amount,
        "increase_amount": statement.increase_amount,
        "decrease_amount": statement.decrease_amount,
        "closing_amount": statement.closing_amount,
        "content_hash": statement.content_hash,
        "status": statement.status,
        "confirmed_at": statement.confirmed_at,
        "created_by": statement.created_by,
        "created_at": statement.created_at,
    }


@router.post("/statements", summary="生成对账单快照", dependencies=[Depends(require_perm("finance:manage"))])
def create_statement(
    payload: StatementCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("finance:manage")),
):
    try:
        statement = receivables.freeze_statement(
            db, payload.customer_id, payload.period_start, payload.period_end, user.username
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    customer = db.get(Customer, payload.customer_id)
    token, code = receivables.issue_share_token(db, statement, phone=customer.phone if customer else "")
    db.commit()
    db.refresh(statement)
    record(
        username=user.username,
        role=user.role,
        action="生成对账单",
        resource=f"对账单 {statement.statement_no}",
        detail=f"客户 {customer.name if customer else ''}，期末 {statement.closing_amount:,.2f}",
        request=request,
    )
    return {
        **_statement_payload(statement),
        "share_path": f"/api/public/s/{token}",
        "share_code": code,
        "payload": receivables.statement_payload(statement),
    }


@router.get("/statements", summary="对账单列表", dependencies=[Depends(require_perm("finance:view"))])
def list_statements(
    customer_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = select(StatementSnapshot)
    if customer_id:
        stmt = stmt.where(StatementSnapshot.customer_id == customer_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(StatementSnapshot.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"total": int(total), "items": [_statement_payload(row) for row in rows]}


@router.get("/statements/{statement_id}", summary="对账单详情与留痕", dependencies=[Depends(require_perm("finance:view"))])
def get_statement(statement_id: int, db: Session = Depends(get_db)):
    statement = db.get(StatementSnapshot, statement_id)
    if not statement:
        raise HTTPException(status_code=404, detail="对账单不存在")
    events = db.scalars(
        select(DocumentEvent)
        .where(DocumentEvent.statement_id == statement_id)
        .order_by(DocumentEvent.id.desc())
        .limit(50)
    ).all()
    return {
        **_statement_payload(statement),
        "payload": receivables.statement_payload(statement),
        "events": [
            {
                "event_type": item.event_type,
                "phone": item.phone,
                "code_verified": item.code_verified,
                "ip": item.ip,
                "detail": item.detail,
                "created_at": item.created_at,
            }
            for item in events
        ],
    }


@router.get(
    "/statements/{statement_id}/export",
    summary="导出对账单 Excel",
    dependencies=[Depends(require_perm("finance:view"))],
)
def export_statement(statement_id: int, db: Session = Depends(get_db)):
    """把冻结快照导出成 Excel，交给客户或会计归档。"""
    statement = db.get(StatementSnapshot, statement_id)
    if not statement:
        raise HTTPException(status_code=404, detail="对账单不存在")
    payload = receivables.statement_payload(statement)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "对账单"
    sheet["A1"] = "对账单"
    sheet["A1"].font = Font(size=16, bold=True)
    sheet["A2"] = f"客户：{payload['customer']['name']}"
    sheet["A3"] = f"账期：{payload['period']['start']} ~ {payload['period']['end']}"
    sheet["A4"] = f"单号：{statement.statement_no}"
    sheet["A5"] = f"内容摘要：{statement.content_hash[:16]}"

    headers = ["日期", "类型", "单号", "摘要", "增加", "减少", "余额"]
    for index, title in enumerate(headers, start=1):
        cell = sheet.cell(row=7, column=index, value=title)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E8EEF9")
        cell.alignment = Alignment(horizontal="center")

    row_index = 8
    sheet.cell(row=row_index, column=1, value=payload["period"]["start"])
    sheet.cell(row=row_index, column=4, value="期初余额")
    sheet.cell(row=row_index, column=7, value=payload["opening"])
    row_index += 1

    for line in payload["lines"]:
        sheet.cell(row=row_index, column=1, value=line["date"])
        sheet.cell(row=row_index, column=2, value="订单" if line["type"] == "order" else "收款")
        sheet.cell(row=row_index, column=3, value=line["no"])
        sheet.cell(row=row_index, column=4, value=line["summary"])
        sheet.cell(row=row_index, column=5, value=line["increase"] or None)
        sheet.cell(row=row_index, column=6, value=line["decrease"] or None)
        sheet.cell(row=row_index, column=7, value=line["balance"])
        row_index += 1

    sheet.cell(row=row_index, column=4, value="期末应收").font = Font(bold=True)
    sheet.cell(row=row_index, column=7, value=payload["closing"]).font = Font(bold=True)

    for column, width in zip("ABCDEFG", (12, 8, 22, 30, 14, 14, 14)):
        sheet.column_dimensions[column].width = width

    buffer = io.BytesIO()
    workbook.save(buffer)
    filename = quote(f"对账单-{statement.statement_no}.xlsx")
    return Response(
        content=buffer.getvalue(),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
