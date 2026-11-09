"""智能核销：为一笔收/付款推断最可能的单据组合。

只读：输入未结清单据与这笔钱的信息，输出核销建议、置信度和可读理由。
真正的落库仍然走 POST /payments、POST /supplier-payments，由用户确认——
核销错了要冲销，代价远高于多点一次「确认」，所以这里不自动写库。

匹配优先级（越靠前越可信）：
1. 流水号里直接出现单号（银行回单常带对方备注）；
2. 金额与某张单据余额完全一致；
3. 金额等于若干张单据余额之和（最多 4 张，组合搜索）；
4. 金额正好结清全部未结；
5. 金额小于最早一笔余额，按部分付款冲抵；
6. 以上都不中，退回先进先出，并明确标注是兜底结果。
"""
import itertools
from datetime import date, datetime

MAX_COMBO_SIZE = 4
# 组合搜索的候选上限：只拿最早的 N 张参与，避免单据多时组合爆炸
MAX_COMBO_CANDIDATES = 28
UNKNOWN_DATE_DISTANCE = 10**6

STRATEGY_LABELS = {
    "reference": "流水号命中",
    "exact": "金额精确匹配",
    "combination": "多单组合匹配",
    "full": "全额结清",
    "partial": "部分付款",
    "fifo": "先进先出兜底",
    "none": "无未结单据",
}

CONFIDENCE = {
    "reference": "high",
    "exact": "high",
    "combination": "high",
    "full": "high",
    "partial": "medium",
    "fifo": "low",
    "none": "low",
}


def _to_cents(value) -> int:
    """金额转整数分，避免浮点累加误差影响「金额是否相等」的判断。"""
    return int(round(float(value or 0) * 100))


def _to_amount(cents: int) -> float:
    return round(cents / 100, 2)


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _normalize(open_orders: list[dict]) -> list[dict]:
    rows = []
    for order in open_orders or []:
        balance_cents = _to_cents(order.get("balance"))
        if balance_cents <= 0:
            continue
        rows.append(
            {
                "order_id": int(order["order_id"]),
                "order_no": str(order.get("order_no") or ""),
                "order_date": str(order.get("order_date") or ""),
                "balance": round(float(order.get("balance") or 0), 2),
                "balance_cents": balance_cents,
                "_date": _parse_date(order.get("order_date")),
            }
        )
    rows.sort(key=lambda row: (row["_date"] or date.min, row["order_id"]))
    return rows


def _date_distance(order: dict, pay_date: date | None) -> int:
    if pay_date is None or order["_date"] is None:
        return UNKNOWN_DATE_DISTANCE
    return abs((pay_date - order["_date"]).days)


def _pick_closest(candidates: list[dict], pay_date: date | None) -> dict:
    """同金额命中多张时，挑日期最接近付款日的那张。"""
    return min(candidates, key=lambda row: (_date_distance(row, pay_date), row["order_id"]))


def _reference_hits(rows: list[dict], reference: str) -> list[dict]:
    """流水号里是否出现单号；比较前去掉空格与分隔符，容忍 SO 2026 1103 这种写法。"""
    text = "".join(ch for ch in (reference or "").lower() if ch.isalnum())
    if not text:
        return []
    hits = []
    for row in rows:
        token = "".join(ch for ch in row["order_no"].lower() if ch.isalnum())
        if len(token) >= 6 and token in text:
            hits.append(row)
    return hits


def _find_combination(rows: list[dict], target_cents: int) -> list[dict] | None:
    """找张数最少的组合，使其余额之和正好等于目标金额。"""
    candidates = [row for row in rows if row["balance_cents"] <= target_cents]
    candidates = candidates[:MAX_COMBO_CANDIDATES]
    upper = min(MAX_COMBO_SIZE, len(candidates))
    for size in range(2, upper + 1):
        for combo in itertools.combinations(candidates, size):
            if sum(row["balance_cents"] for row in combo) == target_cents:
                return list(combo)
    return None


def _allocation(order: dict, cents: int, reason: str) -> dict:
    return {
        "order_id": order["order_id"],
        "order_no": order["order_no"],
        "order_date": order["order_date"],
        "balance": order["balance"],
        "amount": _to_amount(cents),
        "partial": cents < order["balance_cents"],
        "reason": reason,
    }


def _build(
    rows: list[dict],
    amount_cents: int,
    strategy: str,
    explanation: str,
    primary_ids: list[int] | None = None,
    primary_reason: str = "",
    pay_date: date | None = None,
) -> dict:
    """先按主匹配顺序分配，余款再按先进先出补足。"""
    by_id = {row["order_id"]: row for row in rows}
    remaining = amount_cents
    allocations: list[dict] = []
    used: set[int] = set()

    for order_id in primary_ids or []:
        order = by_id.get(order_id)
        if order is None or order_id in used:
            continue
        take = min(order["balance_cents"], remaining)
        if take <= 0:
            continue
        allocations.append(_allocation(order, take, primary_reason))
        remaining -= take
        used.add(order_id)

    for order in rows:
        if remaining <= 0:
            break
        if order["order_id"] in used:
            continue
        take = min(order["balance_cents"], remaining)
        if take <= 0:
            continue
        reason = "余款按先进先出分摊" if primary_ids else "按先进先出分摊"
        allocations.append(_allocation(order, take, reason))
        remaining -= take

    warnings: list[str] = []
    if remaining > 0:
        warnings.append(f"未分配 {_to_amount(remaining):,.2f}，将作为预收/预付挂账")
    partial_count = sum(1 for item in allocations if item["partial"])
    if partial_count:
        warnings.append(f"{partial_count} 笔为部分核销，对应单据仍有未结余额")

    return {
        "amount": _to_amount(amount_cents),
        "allocated": _to_amount(amount_cents - remaining),
        "unallocated": _to_amount(remaining),
        "strategy": strategy,
        "strategy_label": STRATEGY_LABELS.get(strategy, strategy),
        "confidence": CONFIDENCE.get(strategy, "low"),
        "explanation": explanation,
        "allocations": allocations,
        "warnings": warnings,
    }


def match(
    open_orders: list[dict],
    amount: float,
    pay_date=None,
    reference: str = "",
) -> dict:
    """给一笔款项算出核销建议。

    `open_orders` 需包含 order_id / order_no / order_date / balance，
    应收用 receivables.open_orders，应付用 payables.open_orders，两者结构一致。
    """
    amount_cents = _to_cents(amount)
    rows = _normalize(open_orders)
    pay_day = _parse_date(pay_date)

    if not rows:
        return {
            "amount": _to_amount(max(amount_cents, 0)),
            "allocated": 0.0,
            "unallocated": _to_amount(max(amount_cents, 0)),
            "strategy": "none",
            "strategy_label": STRATEGY_LABELS["none"],
            "confidence": CONFIDENCE["none"],
            "explanation": "该往来单位当前没有未结清单据，这笔款项将作为预收/预付挂账",
            "allocations": [],
            "warnings": [],
        }

    if amount_cents <= 0:
        return {
            "amount": 0.0,
            "allocated": 0.0,
            "unallocated": 0.0,
            "strategy": "none",
            "strategy_label": STRATEGY_LABELS["none"],
            "confidence": CONFIDENCE["none"],
            "explanation": "金额必须大于 0 才能核销",
            "allocations": [],
            "warnings": [],
        }

    hits = _reference_hits(rows, reference)
    if hits:
        numbers = "、".join(row["order_no"] for row in hits[:3])
        return _build(
            rows,
            amount_cents,
            "reference",
            f"流水号中出现 {numbers}，优先按单号核销",
            [row["order_id"] for row in hits],
            "流水号包含该单号",
            pay_day,
        )

    exact = [row for row in rows if abs(row["balance_cents"] - amount_cents) <= 1]
    if exact:
        chosen = _pick_closest(exact, pay_day)
        return _build(
            rows,
            amount_cents,
            "exact",
            f"金额与 {chosen['order_no']} 的未结余额完全一致",
            [chosen["order_id"]],
            "金额与该单未结余额一致",
            pay_day,
        )

    combo = _find_combination(rows, amount_cents)
    if combo:
        numbers = "、".join(row["order_no"] for row in combo)
        return _build(
            rows,
            amount_cents,
            "combination",
            f"金额恰好等于 {len(combo)} 张单据余额之和（{numbers}）",
            [row["order_id"] for row in combo],
            f"与另外 {len(combo) - 1} 张单据合计等于本次金额",
            pay_day,
        )

    total_cents = sum(row["balance_cents"] for row in rows)
    if amount_cents == total_cents:
        return _build(
            rows,
            amount_cents,
            "full",
            f"金额与全部 {len(rows)} 张未结单据合计一致，一次结清",
            [row["order_id"] for row in rows],
            "全额结清",
            pay_day,
        )

    oldest = rows[0]
    if amount_cents < oldest["balance_cents"]:
        return _build(
            rows,
            amount_cents,
            "partial",
            f"金额小于最早一笔 {oldest['order_no']} 的未结余额，按部分付款冲抵",
            [oldest["order_id"]],
            "部分付款，优先冲抵最早未结单据",
            pay_day,
        )

    return _build(
        rows,
        amount_cents,
        "fifo",
        "未找到更精确的对应关系，按未结时间先进先出分摊",
        None,
        "",
        pay_day,
    )
