"""对账单公开页：给客户的免登录确认入口。

安全设计：
- 链接只带随机 token；另有一次性验证码（配置短信时）或固定口令（未配置时），二者缺一不可；
- 页面只渲染生成时冻结的快照，不读实时数据，改动已发出的版本不会「对完又变」；
- 每次查看、确认、异议都写留痕（时间、IP、UA、验证码校验结果），可导出举证。
"""
import time
from datetime import datetime
from html import escape

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import StatementSnapshot
from ..services import receivables

router = APIRouter(prefix="/api/public", tags=["对账单（公开）"])

# 发送验证码的冷却时间（秒），避免被反复触发产生短信费
_SEND_COOLDOWN = 60
_last_send: dict[str, float] = {}

STYLE = """
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 20px;
    font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #f5f7fa; color: #1f2937; font-size: 14px; line-height: 1.6;
  }
  .sheet { max-width: 860px; margin: 0 auto; background: #fff; border-radius: 10px;
           box-shadow: 0 2px 12px rgba(15,23,42,.08); padding: 24px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .meta { color: #6b7280; font-size: 12px; margin-bottom: 16px; }
  .cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 18px; }
  .card { background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px 12px; }
  .card span { display: block; font-size: 12px; color: #6b7280; }
  .card b { font-size: 17px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { border-bottom: 1px solid #eef2f7; padding: 8px 6px; text-align: left; }
  th { background: #f8fafc; font-weight: 600; }
  td.num, th.num { text-align: right; }
  .actions { margin-top: 20px; padding-top: 16px; border-top: 1px solid #eef2f7; }
  input, textarea {
    width: 100%; padding: 10px 12px; border: 1px solid #d1d5db; border-radius: 8px;
    font-size: 14px; font-family: inherit;
  }
  button {
    padding: 10px 18px; border: none; border-radius: 8px; font-size: 14px; cursor: pointer;
  }
  .primary { background: #2563eb; color: #fff; }
  .ghost { background: #f1f5f9; color: #334155; }
  .link-btn { background: none; color: #2563eb; padding: 0; text-decoration: underline; }
  .tip { color: #6b7280; font-size: 12px; margin-top: 8px; }
  .ok { color: #16a34a; font-weight: 600; }
  .warn { color: #b45309; }
  .hash { font-family: ui-monospace, Consolas, monospace; font-size: 12px; color: #6b7280; word-break: break-all; }
  .row { display: flex; gap: 10px; align-items: center; }
  .row > *:first-child { flex: 1; }
  @media (max-width: 640px) { .cards { grid-template-columns: repeat(2, 1fr); } }
"""


def _money(value) -> str:
    return f"¥{float(value or 0):,.2f}"


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{escape(title)}</title><style>{STYLE}</style></head>
<body><div class="sheet">{body}</div></body></html>"""
    )


def _code_form(token: str, message: str = "", notice: str = "") -> HTMLResponse:
    """入口页：短信模式显示「获取验证码」，口令模式只显示输入框。"""
    sms_mode = settings.sms_enabled
    label = "短信验证码" if sms_mode else "6 位确认口令"
    hint = (
        f"验证码会发到客户预留手机号，有效期 {settings.SMS_CODE_TTL_MINUTES} 分钟。"
        if sms_mode
        else "口令与链接分开下发的目的是：即使链接被转发，没有口令也无法查看。"
    )
    send_html = (
        f"""
  <form method="post" action="/api/public/s/{escape(token)}/send-code" style="margin-bottom:12px">
    <button class="link-btn" type="submit">没收到？点此获取验证码</button>
  </form>"""
        if sms_mode
        else ""
    )
    body = f"""
  <h1>对账单确认</h1>
  <p class="meta">{'请输入短信验证码' if sms_mode else '请输入业务员发给你的确认口令'}</p>
  {f'<p class="ok">{escape(notice)}</p>' if notice else ''}
  {f'<p class="warn">{escape(message)}</p>' if message else ''}
  {send_html}
  <form method="post" action="/api/public/s/{escape(token)}">
    <input name="code" inputmode="numeric" maxlength="6" placeholder="{escape(label)}" autofocus />
    <p class="tip">{escape(hint)}</p>
    <div class="actions"><button class="primary" type="submit">查看对账单</button></div>
  </form>
"""
    return _page("对账单确认", body)


def _statement_view(
    token: str, statement: StatementSnapshot, payload: dict, code: str, message: str = ""
) -> HTMLResponse:
    customer = payload.get("customer", {})
    period = payload.get("period", {})
    rows = "".join(
        f"<tr><td>{escape(line['date'])}</td><td>{escape(line['no'])}</td>"
        f"<td>{escape(line['summary'])}</td>"
        f"<td class='num'>{_money(line['increase']) if line['increase'] else ''}</td>"
        f"<td class='num'>{_money(line['decrease']) if line['decrease'] else ''}</td>"
        f"<td class='num'>{_money(line['balance'])}</td></tr>"
        for line in payload.get("lines", [])
    )
    confirmed = statement.status == "confirmed"
    confirmed_note = (
        f"<p class='ok'>已于 {statement.confirmed_at:%Y-%m-%d %H:%M} 确认</p>"
        if confirmed and statement.confirmed_at
        else ""
    )
    # 确认表单需要回传验证码：短信码在确认时才消费，口令本身可重复使用
    action_html = (
        confirmed_note
        if confirmed
        else f"""
  <form method="post" action="/api/public/s/{escape(token)}/action">
    <input type="hidden" name="code" value="{escape(code)}" />
    <p><b>如有异议</b>，请在下面写明（例如：第 3 行数量不符 / 10-11 已付款未体现）：</p>
    <textarea name="note" rows="3" placeholder="选填；确认无误可留空"></textarea>
    <div class="actions">
      <button class="primary" name="action" value="confirm" type="submit">确认无误</button>
      <button class="ghost" name="action" value="dispute" type="submit">提出异议</button>
    </div>
    <p class="tip">点击后系统会记录时间、IP 和本页内容摘要，作为双方对账依据。</p>
  </form>
"""
    )
    body = f"""
  <h1>对账单 {escape(statement.statement_no)}</h1>
  <p class="meta">
    {escape(str(customer.get('name', '')))} ·
    账期 {escape(str(period.get('start', '')))} 至 {escape(str(period.get('end', '')))} ·
    第 {statement.version} 版
  </p>
  {f'<p class="ok">{escape(message)}</p>' if message else ''}
  <div class="cards">
    <div class="card"><span>期初余额</span><b>{_money(payload.get('opening'))}</b></div>
    <div class="card"><span>本期增加</span><b>{_money(payload.get('increase'))}</b></div>
    <div class="card"><span>本期减少</span><b>{_money(payload.get('decrease'))}</b></div>
    <div class="card"><span>期末应收</span><b>{_money(payload.get('closing'))}</b></div>
  </div>
  <table>
    <thead><tr><th>日期</th><th>单号</th><th>摘要</th>
      <th class="num">增加</th><th class="num">减少</th><th class="num">余额</th></tr></thead>
    <tbody>{rows or '<tr><td colspan="6">本期无发生额</td></tr>'}</tbody>
  </table>
  {action_html}
  <p class="tip">内容摘要（SHA-256 前 16 位）：<span class="hash">{escape(statement.content_hash[:16])}</span><br />
  本页为生成时冻结的版本，我方后续改动不会影响本页内容。</p>
"""
    return _page(f"对账单 {statement.statement_no}", body)


@router.get("/s/{token}", response_class=HTMLResponse, summary="对账单确认入口")
def statement_entry(token: str):
    """先要验证码，不泄露任何客户信息。"""
    return _code_form(token)


@router.post("/s/{token}/send-code", response_class=HTMLResponse, summary="发送短信验证码")
def send_code(token: str, db: Session = Depends(get_db)):
    share, error = receivables.resolve_share(db, token)
    if not share:
        return _code_form(token, error)
    if not settings.sms_enabled:
        return _code_form(token, "当前未启用短信，请使用业务员提供的口令")

    now = time.time()
    if now - _last_send.get(token, 0) < _SEND_COOLDOWN:
        return _code_form(token, "请稍后再获取，验证码已发送")

    phone = share.phone or ""
    if not phone:
        return _code_form(token, "该客户未登记手机号，请联系业务员")

    code, message = receivables.issue_sms_code(db, share, phone)
    db.commit()
    if not code:
        return _code_form(token, f"短信发送失败：{message}")

    _last_send[token] = now
    masked = f"{phone[:3]}****{phone[-4:]}" if len(phone) >= 7 else phone
    return _code_form(token, "", notice=f"验证码已发送至 {masked}")


@router.post("/s/{token}", response_class=HTMLResponse, summary="验证码校验后展示对账单")
def statement_open(
    token: str,
    request: Request,
    code: str = Form(""),
    db: Session = Depends(get_db),
):
    share, error = receivables.verify_share(db, token, code)
    if not share:
        receivables.record_event(
            db,
            None,
            "deny",
            ip=request.client.host if request.client else "",
            user_agent=request.headers.get("user-agent", ""),
            detail=error,
        )
        db.commit()
        return _code_form(token, error)

    statement = db.get(StatementSnapshot, share.statement_id)
    if not statement:
        return _code_form(token, "对账单不存在，请联系业务员")

    share.used_count += 1
    receivables.record_event(
        db,
        statement.id,
        "view",
        phone=share.phone,
        code_verified=True,
        ip=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
        detail="短信验证码" if settings.sms_enabled else "口令",
    )
    db.commit()
    return _statement_view(token, statement, receivables.statement_payload(statement), code)


@router.post("/s/{token}/action", response_class=HTMLResponse, summary="确认或提出异议")
def statement_action(
    token: str,
    request: Request,
    code: str = Form(""),
    action: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    """确认时消费验证码：短信码一次有效，口令可重复使用。"""
    share, error = receivables.verify_share(db, token, code, consume=True)
    if not share:
        return _code_form(token, error)

    statement = db.get(StatementSnapshot, share.statement_id)
    if not statement:
        return _code_form(token, "对账单不存在，请联系业务员")
    if statement.status == "confirmed":
        return _statement_view(token, statement, receivables.statement_payload(statement), code, "该对账单已确认，无需重复操作")

    if action == "confirm":
        statement.status = "confirmed"
        statement.confirmed_at = datetime.now()
        event_type = "confirm"
    elif action == "dispute":
        statement.status = "disputed"
        event_type = "dispute"
    else:
        return _statement_view(token, statement, receivables.statement_payload(statement), code, "未知操作")

    receivables.record_event(
        db,
        statement.id,
        event_type,
        phone=share.phone,
        code_verified=True,
        ip=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
        detail=(note or "")[:1000],
    )
    db.commit()

    message = "已确认，感谢配合！" if event_type == "confirm" else "异议已记录，我方会尽快联系你。"
    return _statement_view(token, statement, receivables.statement_payload(statement), code, message)
