"""总账服务：会计科目初始化、记账凭证自动生成、三大财务报表。

凭证采用「复式记账、借贷必相等」。自动过账从业务单据（销售订单、收款单、
采购入库、付款单）读取数据，按 source_no 幂等可重算；手工凭证不受影响。

税务口径说明：销项/进项税在「发票管理」模块单独登记，本模块先按不含税口径
（全额计入收入/存货/往来）生成凭证，后续可接入应交税费科目拆分。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (
    Account,
    AccountPeriod,
    Payment,
    Product,
    PurchaseOrder,
    SalesOrder,
    SalesOrderItem,
    SupplierPayment,
    Voucher,
    VoucherLine,
)

# 中国小企业会计准则 一级科目表：(编码, 名称, 类别, 正常余额方向)
CHART_OF_ACCOUNTS: list[tuple[str, str, str, str]] = [
    ("1001", "库存现金", "资产", "dr"),
    ("1002", "银行存款", "资产", "dr"),
    ("1012", "其他货币资金", "资产", "dr"),
    ("1122", "应收账款", "资产", "dr"),
    ("1123", "预付账款", "资产", "dr"),
    ("1221", "其他应收款", "资产", "dr"),
    ("1403", "原材料", "资产", "dr"),
    ("1405", "库存商品", "资产", "dr"),
    ("1411", "委托加工物资", "资产", "dr"),
    ("1601", "固定资产", "资产", "dr"),
    ("1602", "累计折旧", "资产", "cr"),
    ("1701", "无形资产", "资产", "dr"),
    ("1702", "累计摊销", "资产", "cr"),
    ("1801", "长期待摊费用", "资产", "dr"),
    ("2001", "短期借款", "负债", "cr"),
    ("2202", "应付账款", "负债", "cr"),
    ("2203", "预收账款", "负债", "cr"),
    ("2211", "应付职工薪酬", "负债", "cr"),
    ("2221", "应交税费", "负债", "cr"),
    ("2241", "其他应付款", "负债", "cr"),
    ("3001", "实收资本", "所有者权益", "cr"),
    ("3002", "资本公积", "所有者权益", "cr"),
    ("3101", "盈余公积", "所有者权益", "cr"),
    ("3103", "本年利润", "所有者权益", "cr"),
    ("3104", "利润分配", "所有者权益", "cr"),
    ("4001", "生产成本", "成本", "dr"),
    ("4101", "制造费用", "成本", "dr"),
    ("5001", "主营业务收入", "损益", "cr"),
    ("5051", "其他业务收入", "损益", "cr"),
    ("5401", "主营业务成本", "损益", "dr"),
    ("5402", "其他业务成本", "损益", "dr"),
    ("5403", "税金及附加", "损益", "dr"),
    ("5601", "销售费用", "损益", "dr"),
    ("5602", "管理费用", "损益", "dr"),
    ("5603", "财务费用", "损益", "dr"),
    ("5711", "营业外收入", "损益", "cr"),
    ("5712", "营业外支出", "损益", "dr"),
    ("5801", "所得税费用", "损益", "dr"),
]

# 自动过账的凭证来源类型（重建时会按此回滚）
AUTO_SOURCE_TYPES = ("sales", "payment", "purchase", "supplier_payment")


def seed_accounts(db: Session) -> int:
    """幂等初始化会计科目，返回科目总数。"""
    existing = {a.code for a in db.query(Account).all()}
    added = 0
    for code, name, category, direction in CHART_OF_ACCOUNTS:
        if code not in existing:
            db.add(Account(code=code, name=name, category=category, direction=direction))
            added += 1
    if added:
        db.commit()
    return db.query(Account).count()


def _month(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m") if dt else ""


def _period_of(date_str: str, default: str) -> str:
    return date_str or default


def _next_no(db: Session, period: str, used: set[str]) -> str:
    seq = 1
    while True:
        no = f"记-{period}-{seq:04d}"
        if no not in used:
            return no
        seq += 1


def _add_voucher(db: Session, period: str, date: datetime, summary: str,
                 source_type: str, source_no: str, lines: list[dict],
                 created_by: str, used: set[str]) -> Voucher:
    """lines: [{account_code, summary, debit, credit}]；自动补累计借贷相等。"""
    total_debit = round(sum(float(l["debit"]) for l in lines), 2)
    total_credit = round(sum(float(l["credit"]) for l in lines), 2)
    if abs(total_debit - total_credit) > 0.005:
        raise ValueError(f"借贷不平衡：借 {total_debit} 贷 {total_credit}，来源 {source_no}")

    v = Voucher(
        voucher_no=_next_no(db, period, used),
        period=period,
        voucher_date=date,
        summary=summary,
        total_debit=total_debit,
        total_credit=total_credit,
        status="posted",
        source_type=source_type,
        source_no=source_no,
        created_by=created_by,
    )
    db.add(v)
    db.flush()
    used.add(v.voucher_no)
    for l in lines:
        acct = l.get("_account")
        db.add(VoucherLine(
            voucher_id=v.id,
            account_code=l["account_code"],
            account_name=acct.name if acct else "",
            summary=l.get("summary", ""),
            debit=round(float(l["debit"]), 2),
            credit=round(float(l["credit"]), 2),
            partner_name=l.get("partner_name", ""),
        ))
    return v


def _account_map(db: Session) -> dict[str, Account]:
    return {a.code: a for a in db.query(Account).all()}


def period_closed(db: Session, period: str) -> bool:
    """期间是否已结账；未登记的期间按未结处理。"""
    row = db.query(AccountPeriod).filter(AccountPeriod.period == period).first()
    return bool(row and row.status == "closed")


def ensure_period_open(db: Session, period: str) -> None:
    """已结账期间禁止改动凭证，直接抛错，让调用方在落库前失败。"""
    if period_closed(db, period):
        raise ValueError(f"期间 {period} 已结账，请先反结账再修改凭证")


def _split_tax(gross: float, rate: float) -> tuple[float, float]:
    """价税分离：gross 为价税合计，rate 为税率(%)，返回 (不含税金额, 税额)。"""
    rate = float(rate or 0)
    if rate <= 0 or float(gross or 0) <= 0:
        return round(float(gross or 0), 2), 0.0
    net = round(float(gross) / (1 + rate / 100), 2)
    return net, round(float(gross) - net, 2)


def carry_forward_voucher(db: Session, period: str, created_by: str) -> Voucher | None:
    """结转损益：把期间内损益类科目余额转入本年利润（3103）。

    只处理有余额的损益科目；无损益发生时不生成凭证。
    返回生成的凭证（或 None），用于记录结转凭证号以便反结账冲回。
    """
    ensure_period_open(db, period)
    accts = _account_map(db)
    bal = _account_balances(db, period)
    lines: list[dict] = []
    for code, a in accts.items():
        if a.category != "损益":
            continue
        d, c = bal.get(code, (0.0, 0.0))
        if abs(d - c) < 0.005:
            continue
        # 收入类（贷方余额）→ 借损益/贷本年利润；费用类（借方余额）→ 借本年利润/贷损益
        if c > d:
            amount = round(c - d, 2)
            lines.append({"account_code": code, "summary": f"结转{a.name}", "debit": amount, "credit": 0.0})
            lines.append({"account_code": "3103", "summary": f"结转{a.name}至本年利润", "debit": 0.0, "credit": amount})
        else:
            amount = round(d - c, 2)
            lines.append({"account_code": code, "summary": f"结转{a.name}", "debit": 0.0, "credit": amount})
            lines.append({"account_code": "3103", "summary": f"结转{a.name}至本年利润", "debit": amount, "credit": 0.0})
    if not lines:
        return None
    for ln in lines:
        ln["_account"] = accts.get(ln["account_code"])
    used = {row[0] for row in db.query(Voucher.voucher_no).filter(Voucher.period == period).all()}
    v = _add_voucher(
        db, period, datetime.now(), f"期末结转损益（{period}）",
        "closing", f"{period}-closing", lines, created_by, used,
    )
    db.commit()
    return v


def _account_balances_year(db: Session, year: str) -> dict[str, tuple[float, float]]:
    """同 _account_balances，但按整年（period LIKE 'YYYY-%'）聚合，且排除结转凭证。"""
    q = (
        db.query(
            VoucherLine.account_code,
            func.coalesce(func.sum(VoucherLine.debit), 0),
            func.coalesce(func.sum(VoucherLine.credit), 0),
        )
        .join(Voucher, Voucher.id == VoucherLine.voucher_id)
        .filter(Voucher.status == "posted")
        .filter(Voucher.source_type != "closing")
        .filter(Voucher.period.like(f"{year}-%"))
        .group_by(VoucherLine.account_code)
    )
    result: dict[str, tuple[float, float]] = {}
    for code, d, c in q.all():
        result[code] = (round(float(d), 2), round(float(c), 2))
    return result


def carry_forward_year(db: Session, year: str, created_by: str) -> Voucher | None:
    """年度结转：把本年利润（损益净额）转入利润分配/未分配利润（3104）。

    仅每年 12 月结账时调用一次，把全年损益结平到未分配利润；
    当年无损益不发生凭证。返回生成的凭证（或 None）。
    """
    accts = _account_map(db)
    bal = _account_balances_year(db, year)
    net = 0.0
    for code, a in accts.items():
        if a.category != "损益":
            continue
        d, c = bal.get(code, (0.0, 0.0))
        net = round(net + (c - d), 2)
    if abs(net) < 0.005:
        return None

    if net > 0:
        lines = [
            {"account_code": "3103", "summary": "结转本年利润", "debit": net, "credit": 0.0},
            {"account_code": "3104", "summary": "转入未分配利润", "debit": 0.0, "credit": net},
        ]
    else:
        lines = [
            {"account_code": "3104", "summary": "结转本年亏损", "debit": abs(net), "credit": 0.0},
            {"account_code": "3103", "summary": "转出本年利润", "debit": 0.0, "credit": abs(net)},
        ]
    for ln in lines:
        ln["_account"] = accts.get(ln["account_code"])

    used = {row[0] for row in db.query(Voucher.voucher_no).filter(Voucher.period == f"{year}-12").all()}
    v = _add_voucher(
        db, f"{year}-12", datetime.now(), f"年度结转损益至未分配利润（{year}）",
        "closing", f"{year}-yearclose", lines, created_by, used,
    )
    db.commit()
    return v


def generate_vouchers(db: Session, period: str, created_by: str = "系统") -> dict:
    """按期间从业务单据重建自动凭证（幂等）。

    顺序：先删除该期间已生成的自动凭证，再重新过账。
    返回 {generated: n, period: ...}。
    """
    accts = _account_map(db)
    ensure_period_open(db, period)

    # 回滚该期间已有的自动凭证
    old_vouchers = (
        db.query(Voucher)
        .filter(Voucher.period == period, Voucher.source_type.in_(AUTO_SOURCE_TYPES))
        .all()
    )
    for v in old_vouchers:
        db.query(VoucherLine).filter(VoucherLine.voucher_id == v.id).delete()
        db.delete(v)
    db.flush()
    # 编号要避开该期间已有的手工凭证，否则会撞上 voucher_no 唯一约束
    used = {row[0] for row in db.query(Voucher.voucher_no).filter(Voucher.period == period).all()}

    count = 0

    # 1) 销售确认 -> 借 应收账款 / 贷 主营业务收入；结转成本 -> 借 主营业务成本 / 贷 库存商品
    sales = (
        db.query(SalesOrder)
        .filter(SalesOrder.status.in_(("confirmed", "completed")))
        .all()
    )
    for so in sales:
        if _month(so.order_date) != period:
            continue
        revenue = round(so.total_amount - (so.discount_amount or 0.0), 2)
        if revenue <= 0:
            continue
        # 价税分离：tax_rate>0 时把价税合计拆成不含税收入与销项税额
        net_revenue, sales_tax = _split_tax(revenue, so.tax_rate)
        # 成本：明细行数量 × 商品成本价
        cost = round(
            float(
                db.scalar(
                    db.query(
                        func.coalesce(
                            func.sum(
                                SalesOrderItem.quantity
                                * func.coalesce(func.nullif(Product.avg_cost, 0), Product.cost_price)
                            ),
                            0,
                        )
                    )
                    .join(Product, Product.id == SalesOrderItem.product_id)
                    .filter(SalesOrderItem.order_id == so.id)
                )
                or 0.0
            ),
            2,
        )
        lines = [
            {"account_code": "1122", "summary": "销售确认应收", "debit": revenue, "credit": 0.0,
             "partner_name": so.customer.name if so.customer else ""},
            {"account_code": "5001", "summary": "主营业务收入", "debit": 0.0, "credit": net_revenue},
        ]
        if sales_tax > 0:
            lines.append({"account_code": "2221", "summary": "销项税额", "debit": 0.0, "credit": sales_tax})
        for ln in lines:
            ln["_account"] = accts.get(ln["account_code"])
        _add_voucher(db, period, so.order_date or so.created_at, f"销售订单 {so.order_no} 确认收入",
                     "sales", so.order_no, lines, created_by, used)
        if cost > 0:
            cost_lines = [
                {"account_code": "5401", "summary": "结转销售成本", "debit": cost, "credit": 0.0},
                {"account_code": "1405", "summary": "销售出库成本", "debit": 0.0, "credit": cost},
            ]
            for ln in cost_lines:
                ln["_account"] = accts.get(ln["account_code"])
            _add_voucher(db, period, so.order_date or so.created_at, f"销售订单 {so.order_no} 结转成本",
                         "sales", f"{so.order_no}-cost", cost_lines, created_by, used)
        count += 1

    # 2) 收款 -> 借 银行存款 / 贷 应收账款
    for p in db.query(Payment).filter(Payment.status == "posted").all():
        if _month(p.pay_date) != period:
            continue
        lines = [
            {"account_code": "1002", "summary": "客户回款", "debit": p.amount, "credit": 0.0,
             "partner_name": p.customer.name if p.customer else ""},
            {"account_code": "1122", "summary": "核销应收", "debit": 0.0, "credit": p.amount,
             "partner_name": p.customer.name if p.customer else ""},
        ]
        for ln in lines:
            ln["_account"] = accts.get(ln["account_code"])
        _add_voucher(db, period, p.pay_date, f"收款单 {p.payment_no}", "payment", p.payment_no,
                     lines, created_by, used)
        count += 1

    # 3) 采购入库 -> 借 原材料 / 贷 应付账款
    for po in db.query(PurchaseOrder).filter(PurchaseOrder.status == "received").all():
        if _month(po.order_date) != period:
            continue
        if po.total_amount <= 0:
            continue
        # 价税分离：tax_rate>0 时把价税合计拆成不含税存货与进项税额
        net_amount, purchase_tax = _split_tax(po.total_amount, po.tax_rate)
        lines = [
            {"account_code": "1403", "summary": "采购入库", "debit": net_amount, "credit": 0.0,
             "partner_name": po.supplier.name if po.supplier else ""},
            {"account_code": "2202", "summary": "形成应付", "debit": 0.0, "credit": po.total_amount,
             "partner_name": po.supplier.name if po.supplier else ""},
        ]
        if purchase_tax > 0:
            lines.append({"account_code": "2221", "summary": "进项税额", "debit": purchase_tax, "credit": 0.0})
        for ln in lines:
            ln["_account"] = accts.get(ln["account_code"])
        _add_voucher(db, period, po.order_date, f"采购单 {po.order_no} 入库", "purchase", po.order_no,
                     lines, created_by, used)
        count += 1

    # 4) 付款 -> 借 应付账款 / 贷 银行存款
    for pp in db.query(SupplierPayment).filter(SupplierPayment.status == "posted").all():
        if _month(pp.pay_date) != period:
            continue
        lines = [
            {"account_code": "2202", "summary": "支付供应商", "debit": pp.amount, "credit": 0.0,
             "partner_name": pp.supplier.name if pp.supplier else ""},
            {"account_code": "1002", "summary": "银行付款", "debit": 0.0, "credit": pp.amount,
             "partner_name": pp.supplier.name if pp.supplier else ""},
        ]
        for ln in lines:
            ln["_account"] = accts.get(ln["account_code"])
        _add_voucher(db, period, pp.pay_date, f"付款单 {pp.payment_no}", "supplier_payment",
                     pp.payment_no, lines, created_by, used)
        count += 1

    db.commit()
    return {"period": period, "generated": count}


def _account_balances(db: Session, period: str = "") -> dict[str, tuple[float, float]]:
    """返回 {account_code: (借记合计, 贷记合计)}，period 为空表示全部累计。

    结账生成的「结转损益」凭证不计入：否则关账后利润表会被清零，
    已结账期间就查不到当期经营成果了。
    """
    q = (
        db.query(
            VoucherLine.account_code,
            func.coalesce(func.sum(VoucherLine.debit), 0),
            func.coalesce(func.sum(VoucherLine.credit), 0),
        )
        .join(Voucher, Voucher.id == VoucherLine.voucher_id)
        .filter(Voucher.status == "posted")
        .filter(Voucher.source_type != "closing")
    )
    if period:
        q = q.filter(Voucher.period == period)
    q = q.group_by(VoucherLine.account_code)
    result: dict[str, tuple[float, float]] = {}
    for code, d, c in q.all():
        result[code] = (round(float(d), 2), round(float(c), 2))
    return result


def _net(balance: tuple[float, float], direction: str) -> float:
    d, c = balance
    return round((d - c) if direction == "dr" else (c - d), 2)


def balance_sheet(db: Session, period: str = "") -> dict:
    """资产负债表：资产 = 负债 + 所有者权益（含未分配利润）。"""
    accts = _account_map(db)
    bal = _account_balances(db, period)

    assets, liabilities, equity_rows = [], [], []
    net_profit = 0.0
    cost_cat_total = 0.0

    for code, a in sorted(accts.items()):
        d, c = bal.get(code, (0.0, 0.0))
        amount = _net((d, c), a.direction)
        if a.category == "损益":
            net_profit = round(net_profit + (c - d), 2)
            continue
        if a.category == "成本":
            cost_cat_total = round(cost_cat_total + amount, 2)
            continue
        if a.category == "资产":
            assets.append({"code": code, "name": a.name, "amount": amount})
        elif a.category == "负债":
            liabilities.append({"code": code, "name": a.name, "amount": amount})
        elif a.category == "所有者权益":
            equity_rows.append({"code": code, "name": a.name, "amount": amount})

    if cost_cat_total:
        assets.append({"code": "*", "name": "成本类科目（在产品等）", "amount": cost_cat_total})

    asset_total = round(sum(x["amount"] for x in assets), 2)
    liability_total = round(sum(x["amount"] for x in liabilities), 2)
    equity_total = round(sum(x["amount"] for x in equity_rows), 2)
    equity_total = round(equity_total + net_profit, 2)
    equity_rows.append({"code": "3103", "name": "本年利润（损益结转）", "amount": net_profit})

    return {
        "period": period,
        "assets": assets,
        "asset_total": asset_total,
        "liabilities": liabilities,
        "liability_total": liability_total,
        "equity": equity_rows,
        "equity_total": equity_total,
        "balanced": abs(asset_total - (liability_total + equity_total)) < 0.01,
    }


def income_statement(db: Session, period: str = "") -> dict:
    """利润表（小企业会计准则口径）。"""
    accts = _account_map(db)
    bal = _account_balances(db, period)

    def amt(code: str) -> float:
        a = accts.get(code)
        if a is None:
            return 0.0
        d, c = bal.get(code, (0.0, 0.0))
        return _net((d, c), a.direction)

    revenue = round(amt("5001") + amt("5051"), 2)
    cost = round(amt("5401") + amt("5402"), 2)
    tax_surcharge = amt("5403")
    sales_exp = amt("5601")
    admin_exp = amt("5602")
    finance_exp = amt("5603")
    operating_profit = round(revenue - cost - tax_surcharge - sales_exp - admin_exp - finance_exp, 2)
    non_op_income = amt("5711")
    non_op_expense = amt("5712")
    total_profit = round(operating_profit + non_op_income - non_op_expense, 2)
    income_tax = amt("5801")
    net_profit = round(total_profit - income_tax, 2)

    return {
        "period": period,
        "revenue": revenue,
        "cost": cost,
        "tax_surcharge": tax_surcharge,
        "sales_expense": sales_exp,
        "admin_expense": admin_exp,
        "finance_expense": finance_exp,
        "operating_profit": operating_profit,
        "non_op_income": non_op_income,
        "non_op_expense": non_op_expense,
        "total_profit": total_profit,
        "income_tax": income_tax,
        "net_profit": net_profit,
    }


def cash_flow_statement(db: Session, period: str = "") -> dict:
    """现金流量表：经营 + 投资 + 筹资三分类。

    经营：来自收款单/付款单（客户回款、供应商付款）。
    投资/筹资：来自手工凭证里「现金 ↔ 投资类/筹资类科目」的净额，
    覆盖购买/处置固定资产、借款与还款、增资、分红等此前被固定为 0 的活动。
    """
    inflow_q = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.status == "posted")
    outflow_q = db.query(func.coalesce(func.sum(SupplierPayment.amount), 0)).filter(SupplierPayment.status == "posted")

    if period:
        inflow_q = inflow_q.filter(func.strftime("%Y-%m", Payment.pay_date) == period)
        outflow_q = outflow_q.filter(func.strftime("%Y-%m", SupplierPayment.pay_date) == period)

    operating_inflow = round(float(db.scalar(inflow_q) or 0.0), 2)
    operating_outflow = round(float(db.scalar(outflow_q) or 0.0), 2)
    operating_net = round(operating_inflow - operating_outflow, 2)

    # 投资/筹资：现金科目对价科目分类
    cash_codes = {"1001", "1002", "1012"}
    investing_codes = {"1601", "1701", "1801", "1602", "1702"}
    financing_codes = {"2001", "3001", "3002", "3101", "3104"}

    investing_inflow = investing_outflow = 0.0
    financing_inflow = financing_outflow = 0.0

    vq = db.query(Voucher).filter(Voucher.status == "posted").filter(Voucher.source_type != "closing")
    if period:
        vq = vq.filter(Voucher.period == period)
    for v in vq.all():
        lines = db.query(VoucherLine).filter(VoucherLine.voucher_id == v.id).all()
        cash_net = round(
            sum((l.debit or 0) - (l.credit or 0) for l in lines if (l.account_code or "")[:4] in cash_codes),
            2,
        )
        if abs(cash_net) < 0.005:
            continue
        noncash_codes = {
            (l.account_code or "")[:4] for l in lines if (l.account_code or "")[:4] not in cash_codes
        }
        if noncash_codes & investing_codes:
            if cash_net > 0:
                investing_inflow = round(investing_inflow + cash_net, 2)
            else:
                investing_outflow = round(investing_outflow - cash_net, 2)
        elif noncash_codes & financing_codes:
            if cash_net > 0:
                financing_inflow = round(financing_inflow + cash_net, 2)
            else:
                financing_outflow = round(financing_outflow - cash_net, 2)

    investing_net = round(investing_inflow - investing_outflow, 2)
    financing_net = round(financing_inflow - financing_outflow, 2)
    net_increase = round(operating_net + investing_net + financing_net, 2)

    return {
        "period": period,
        "operating_inflow": operating_inflow,
        "operating_outflow": operating_outflow,
        "operating_net": operating_net,
        "investing_inflow": investing_inflow,
        "investing_outflow": investing_outflow,
        "investing_net": investing_net,
        "financing_inflow": financing_inflow,
        "financing_outflow": financing_outflow,
        "financing_net": financing_net,
        "net_increase": net_increase,
    }