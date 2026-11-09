"""银行流水导入与自动对账接口。"""
from datetime import date, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_perm
from ..models import BankStatement, BankStatementMatch, User
from ..services import bank, importer

router = APIRouter(prefix="/api/bank", tags=["银行对账"])

STATEMENT_COLUMNS = ["交易日期", "方向", "金额", "对方户名", "流水号", "摘要", "银行账号"]
_TEMPLATE_SAMPLE = [["2026-11-07", "收入", 1000.00, "杭州远航机电有限公司", "BANK20261107001", "货款", "6222 8888 0001"]]


def _parse_date(value):
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法识别的日期：{value}")


def _statement_payload(row: BankStatement) -> dict:
    return {
        "id": row.id,
        "bank_account": row.bank_account,
        "ref_no": row.ref_no,
        "trans_date": row.trans_date.strftime("%Y-%m-%d") if row.trans_date else "",
        "direction": row.direction,
        "amount": round(float(row.amount), 2),
        "counterparty": row.counterparty,
        "summary": row.summary,
        "status": row.status,
        "matched_type": row.matched_type,
        "matched_id": row.matched_id,
        "created_by": row.created_by,
    }


@router.get("/statements", summary="银行流水列表", dependencies=[Depends(require_perm("bank:view"))])
def list_statements(
    status: str | None = Query(None),
    keyword: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = select(BankStatement)
    if status:
        stmt = stmt.where(BankStatement.status == status)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(BankStatement.counterparty.like(like))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(BankStatement.trans_date.desc(), BankStatement.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"total": total, "items": [_statement_payload(r) for r in rows]}


@router.get("/matches", summary="对账建议列表", dependencies=[Depends(require_perm("bank:view"))])
def list_matches(
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    stmt = select(BankStatementMatch).order_by(BankStatementMatch.id.desc())
    if status:
        stmt = stmt.where(BankStatementMatch.status == status)
    rows = db.scalars(stmt).all()
    return [
        {
            "id": row.id,
            "statement_id": row.statement_id,
            "partner_type": row.partner_type,
            "partner_id": row.partner_id,
            "partner_name": row.partner_name,
            "amount": round(float(row.amount), 2),
            "confidence": row.confidence,
            "strategy": row.strategy,
            "status": row.status,
            "applied_id": row.applied_id,
        }
        for row in rows
    ]


@router.get("/template", dependencies=[Depends(require_perm("bank:view"))])
def bank_template():
    content = importer.build_template(STATEMENT_COLUMNS, _TEMPLATE_SAMPLE, sheet_name="银行流水导入")
    return Response(
        content=content,
        media_type=importer.XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": "attachment; filename*=UTF-8''bank_statement_import_template.xlsx"},
    )


@router.post("/import", dependencies=[Depends(require_perm("bank:manage"))])
def import_statements(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        rows = importer.parse_workbook(file.file.read(), STATEMENT_COLUMNS)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    data: list[dict] = []
    for row in rows:
        try:
            data.append(
                {
                    "trans_date": _parse_date(row.get("交易日期")),
                    "direction": str(row.get("方向") or "").strip(),
                    "amount": importer.parse_number(row.get("金额")),
                    "counterparty": str(row.get("对方户名") or "").strip(),
                    "ref_no": str(row.get("流水号") or "").strip(),
                    "summary": str(row.get("摘要") or "").strip(),
                    "bank_account": str(row.get("银行账号") or "").strip(),
                }
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"第 {row['_row']} 行：{exc}") from exc

    result = bank.import_statements(db, data, user.username)
    return result


@router.post("/matches/{match_id}/apply", summary="确认对账建议（生成收/付款单）", dependencies=[Depends(require_perm("bank:manage"))])
def apply_match(
    match_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return bank.apply_match(db, match_id, user.username)


@router.post("/matches/{match_id}/dismiss", summary="忽略对账建议", dependencies=[Depends(require_perm("bank:manage"))])
def dismiss_match(match_id: int, db: Session = Depends(get_db)):
    return bank.dismiss_match(db, match_id)
