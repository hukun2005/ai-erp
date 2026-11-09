"""应付、付款与供应商对账单接口。"""
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
from ..models import Supplier, SupplierPayment, SupplierStatement, User
from ..schemas import SupplierPaymentCreate, SupplierPaymentMatchRequest, SupplierStatementCreate
from ..services import approval_flow, approvals, payables, reconciliation
from ..services.importer import XLSX_MEDIA_TYPE

router = APIRouter(prefix="/api", tags=["应付与对账"])


def _next_payment_no(db: Session) -> str:
    today = datetime.now().strftime("%Y%m%d")
    count = db.scalar(
        select(func.count()).select_from(SupplierPayment).where(SupplierPayment.payment_no.like(f"PY{today}%"))
    ) or 0
    return f"PY{today}{int(count) + 1:04d}"


def _payment_payload(payment: SupplierPayment) -> dict:
    return {
        "id": payment.id,
        "payment_no": payment.payment_no,
        "supplier_id": payment.supplier_id,
        "supplier_name": payment.supplier.name if payment.supplier else "",
        "amount": round(float(payment.amount), 2),
        "pay_date": payment.pay_date,
        "method": payment.method,
        "reference": payment.reference,
        "remark": payment.remark,
        "status": payment.status,
        "approval_required": int(payment.approval_required or 0),
        "approval_approved": int(payment.approval_approved or 0),
        "void_reason": payment.void_reason,
        "created_by": payment.created_by,
        "allocations": [
            {"order_id": item.order_id, "amount": round(float(item.amount), 2)}
            for item in payment.allocations
        ],
    }


def _statement_payload(statement: SupplierStatement) -> dict:
    return {
        "id": statement.id,
        "statement_no": statement.statement_no,
        "supplier_id": statement.supplier_id,
        "supplier_name": statement.supplier.name if statement.supplier else "",
        "period_start": statement.period_start,
        "period_end": statement.period_end,
        "version": statement.version,
        "opening_amount": statement.opening_amount,
        "increase_amount": statement.increase_amount,
        "decrease_amount": statement.decrease_amount,
        "closing_amount": statement.closing_amount,
        "content_hash": statement.content_hash,
        "created_by": statement.created_by,
        "created_at": statement.created_at,
    }


# ---------------- 应付 ----------------
@router.get("/payables", summary="供应商应付账龄", dependencies=[Depends(require_perm("payable:view"))])
def list_payables(db: Session = Depends(get_db)):
    items = payables.supplier_payables(db)
    buckets = {"0-30天": 0.0, "31-60天": 0.0, "60天以上": 0.0}
    for item in items:
        buckets[item["bucket"]] = round(buckets[item["bucket"]] + item["balance"], 2)
    return {
        "total": payables.payable_total(db),
        "buckets": [{"name": key, "value": value} for key, value in buckets.items()],
        "items": items,
    }


@router.get(
    "/payables/{supplier_id}/orders",
    summary="供应商未结清入库单（核销候选）",
    dependencies=[Depends(require_perm("payable:view"))],
)
def supplier_open_orders(supplier_id: int, db: Session = Depends(get_db)):
    return payables.open_orders(db, supplier_id)


@router.post(
    "/supplier-payments/match",
    summary="智能核销建议（只算不写）",
    dependencies=[Depends(require_perm("payable:manage"))],
)
def match_supplier_payment(payload: SupplierPaymentMatchRequest, db: Session = Depends(get_db)):
    """按流水号、金额与账龄给出核销建议；确认后仍走 POST /supplier-payments 落库。"""
    supplier = db.get(Supplier, payload.supplier_id)
    if not supplier:
        raise HTTPException(status_code=400, detail="供应商不存在")
    result = reconciliation.match(
        payables.open_orders(db, payload.supplier_id),
        payload.amount,
        payload.pay_date,
        payload.reference,
    )
    result["party"] = {"id": supplier.id, "name": supplier.name}
    return result



@router.get("/supplier-payments", summary="付款列表", dependencies=[Depends(require_perm("payable:view"))])
def list_payments(
    supplier_id: int | None = Query(None),
    keyword: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = select(SupplierPayment).join(Supplier, Supplier.id == SupplierPayment.supplier_id)
    if supplier_id:
        stmt = stmt.where(SupplierPayment.supplier_id == supplier_id)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            SupplierPayment.payment_no.like(like)
            | Supplier.name.like(like)
            | SupplierPayment.reference.like(like)
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(SupplierPayment.pay_date.desc(), SupplierPayment.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"total": int(total), "items": [_payment_payload(row) for row in rows]}


@router.post("/supplier-payments", summary="登记付款并核销", dependencies=[Depends(require_perm("payable:manage"))])
def create_payment(
    payload: SupplierPaymentCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("payable:manage")),
):
    supplier = db.get(Supplier, payload.supplier_id)
    if not supplier:
        raise HTTPException(status_code=400, detail="供应商不存在")

    # 配置了付款审批规则时先挂起，审批通过才转 posted 并计入应付核销
    pending = approval_flow.has_rules(db, "supplier_payment")
    payment = SupplierPayment(
        payment_no=_next_payment_no(db),
        supplier_id=payload.supplier_id,
        amount=round(float(payload.amount), 2),
        pay_date=payload.pay_date or datetime.now(),
        method=payload.method or "银行转账",
        reference=payload.reference or "",
        remark=payload.remark or "",
        status="pending" if pending else "posted",
        created_by=user.username,
    )
    try:
        payables.allocate(db, payment, [item.model_dump() for item in payload.allocations])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.add(payment)
    db.flush()
    if pending:
        payment.approval_required = approval_flow.required_levels(
            db, "supplier_payment", payment.amount,
            approval_flow.context_for(db, "supplier_payment", payment),
        )
        payment.approval_approved = 0
        approvals.log_action(db, "supplier_payment", payment.id, "submit", user)
    db.commit()
    db.refresh(payment)
    record(
        username=user.username,
        role=user.role,
        action="登记付款",
        resource=f"付款单 {payment.payment_no}",
        detail=(
            f"供应商 {supplier.name}，金额 {payment.amount:,.2f}，核销 {len(payment.allocations)} 单"
            + ("（待审批）" if pending else "")
        ),
        request=request,
    )
    return _payment_payload(payment)


@router.post(
    "/supplier-payments/{payment_id}/void",
    summary="作废付款",
    dependencies=[Depends(require_perm("payable:manage"))],
)
def void_payment(
    payment_id: int,
    request: Request,
    reason: str = Query("", max_length=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("payable:manage")),
):
    payment = db.get(SupplierPayment, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="付款单不存在")
    if payment.status == "voided":
        raise HTTPException(status_code=400, detail="该付款单已作废")
    payment.status = "voided"
    payment.void_reason = reason or "未填写原因"
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="作废付款",
        resource=f"付款单 {payment.payment_no}",
        detail=payment.void_reason,
        request=request,
    )
    return {"ok": True}


@router.post("/supplier-payments/{payment_id}/approve", summary="审批通过付款单（多级审批）")
def approve_payment(
    payment_id: int,
    request: Request,
    comment: str = Query("", description="审批意见"),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("payable:approve")),
):
    payment = db.get(SupplierPayment, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="付款单不存在")
    if payment.status != "pending":
        raise HTTPException(status_code=400, detail="该付款单不处于待审批状态")
    result = approval_flow.perform_approval(db, "supplier_payment", payment, user, comment)
    if result["completed"]:
        payment.status = "posted"
    db.commit()
    db.refresh(payment)
    record(
        username=user.username,
        role=user.role,
        action="付款单审批通过",
        resource=f"付款单 {payment.payment_no}",
        detail=f"第 {result['level']}/{result['required']} 级" + ("" if result["level_complete"] else "（会签未齐）"),
        request=request,
    )
    payload = _payment_payload(payment)
    payload["pending_next_level"] = result["level_complete"]
    return payload


@router.post("/supplier-payments/{payment_id}/reject", summary="审批驳回付款单")
def reject_payment(
    payment_id: int,
    request: Request,
    comment: str = Query("", description="驳回原因"),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("payable:approve")),
):
    payment = db.get(SupplierPayment, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="付款单不存在")
    if payment.status != "pending":
        raise HTTPException(status_code=400, detail="该付款单不处于待审批状态")
    level, _rule = approval_flow.ensure_level_permission(db, "supplier_payment", payment, user)
    payment.status = "voided"
    payment.void_reason = (comment or "审批驳回")[:200]
    payment.approval_approved = 0
    approvals.log_action(db, "supplier_payment", payment.id, "reject", user, comment or "驳回", level=level)
    db.commit()
    db.refresh(payment)
    record(
        username=user.username,
        role=user.role,
        action="付款单审批驳回",
        resource=f"付款单 {payment.payment_no}",
        detail=payment.void_reason,
        request=request,
    )
    return _payment_payload(payment)


# ---------------- 供应商对账单 ----------------
@router.post(
    "/supplier-statements",
    summary="生成供应商对账单快照",
    dependencies=[Depends(require_perm("payable:manage"))],
)
def create_statement(
    payload: SupplierStatementCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("payable:manage")),
):
    supplier = db.get(Supplier, payload.supplier_id)
    if not supplier:
        raise HTTPException(status_code=400, detail="供应商不存在")
    try:
        statement = payables.freeze_statement(
            db, payload.supplier_id, payload.period_start, payload.period_end, user.username
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.commit()
    db.refresh(statement)
    record(
        username=user.username,
        role=user.role,
        action="生成供应商对账单",
        resource=f"供应商对账单 {statement.statement_no}",
        detail=f"供应商 {supplier.name}，期末应付 {statement.closing_amount:,.2f}",
        request=request,
    )
    return {
        **_statement_payload(statement),
        "payload": payables.statement_payload(statement),
    }


@router.get(
    "/supplier-statements",
    summary="供应商对账单列表",
    dependencies=[Depends(require_perm("payable:view"))],
)
def list_statements(
    supplier_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = select(SupplierStatement)
    if supplier_id:
        stmt = stmt.where(SupplierStatement.supplier_id == supplier_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(SupplierStatement.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"total": int(total), "items": [_statement_payload(row) for row in rows]}


@router.get(
    "/supplier-statements/{statement_id}",
    summary="供应商对账单详情",
    dependencies=[Depends(require_perm("payable:view"))],
)
def get_statement(statement_id: int, db: Session = Depends(get_db)):
    statement = db.get(SupplierStatement, statement_id)
    if not statement:
        raise HTTPException(status_code=404, detail="对账单不存在")
    return {
        **_statement_payload(statement),
        "payload": payables.statement_payload(statement),
    }


@router.get(
    "/supplier-statements/{statement_id}/export",
    summary="导出供应商对账单 Excel",
    dependencies=[Depends(require_perm("payable:view"))],
)
def export_statement(statement_id: int, db: Session = Depends(get_db)):
    statement = db.get(SupplierStatement, statement_id)
    if not statement:
        raise HTTPException(status_code=404, detail="对账单不存在")
    payload = payables.statement_payload(statement)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "供应商对账单"
    sheet["A1"] = "供应商对账单"
    sheet["A1"].font = Font(size=16, bold=True)
    sheet["A2"] = f"供应商：{payload['supplier']['name']}"
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
    sheet.cell(row=row_index, column=4, value="期初应付")
    sheet.cell(row=row_index, column=7, value=payload["opening"])
    row_index += 1

    type_labels = {"order": "采购入库", "return": "采购退货", "payment": "付款"}
    for line in payload["lines"]:
        sheet.cell(row=row_index, column=1, value=line["date"])
        sheet.cell(row=row_index, column=2, value=type_labels.get(line["type"], line["type"]))
        sheet.cell(row=row_index, column=3, value=line["no"])
        sheet.cell(row=row_index, column=4, value=line["summary"])
        sheet.cell(row=row_index, column=5, value=line["increase"] or None)
        sheet.cell(row=row_index, column=6, value=line["decrease"] or None)
        sheet.cell(row=row_index, column=7, value=line["balance"])
        row_index += 1

    sheet.cell(row=row_index, column=4, value="期末应付").font = Font(bold=True)
    sheet.cell(row=row_index, column=7, value=payload["closing"]).font = Font(bold=True)

    for column, width in zip("ABCDEFG", (12, 10, 22, 30, 14, 14, 14)):
        sheet.column_dimensions[column].width = width

    buffer = io.BytesIO()
    workbook.save(buffer)
    filename = quote(f"供应商对账单-{statement.statement_no}.xlsx")
    return Response(
        content=buffer.getvalue(),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
