"""AI 服务层。

设计要点：
1. 所有能力都建立在真实的业务数据之上（库存、订单、客户），不依赖大模型也能用；
2. 配置了 LLM_API_KEY 后，问答升级为「大模型 + 工具调用」：模型只负责选工具和填参数，
   取数口径、鉴权与数据范围全部由工具层保证；
3. 未配置或调用失败时，自动降级为内置规则引擎，保证系统永远可用。

大模型链路的准确率主要取决于三件事：提示词里的口径与时间、工具覆盖面、工具结果的
可读性。因此这里不做「把整库快照塞进提示词」的事——快照会和工具返回的数字冲突，
也会让模型偷懒不调用工具。
"""
import json
import logging
import time
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..ai.tools import available_tools, execute
from ..ai.tools.base import ToolContext, ToolResult
from ..config import settings
from ..models import (
    Product,
    PurchaseOrder,
    PurchaseReturn,
    SalesOrder,
    SalesOrderItem,
    SalesReturn,
    StocktakeOrder,
)
from ..permissions import insight_types_for, permissions_for, role_meta
from ..scoping import scope_owner
from . import analytics
from . import approvals as approval_service
from . import payables as ap_service
from . import receivables as ar_service
from .replenishment import get_replenishment

logger = logging.getLogger(__name__)


def _money(value: float) -> str:
    return f"¥{value:,.2f}"


def _finance_summary(db: Session, days: int = 30) -> dict:
    """营收 / 成本 / 毛利 / 应收 汇总。"""
    start = (datetime.now() - timedelta(days=days - 1)).date().isoformat()
    revenue = db.scalar(
        select(func.coalesce(func.sum(SalesOrder.total_amount), 0))
        .where(SalesOrder.status.in_(analytics.VALID_SALES_STATUS))
        .where(func.date(SalesOrder.order_date) >= start)
    ) or 0
    cost = db.scalar(
        select(func.coalesce(func.sum(SalesOrderItem.quantity * Product.cost_price), 0))
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
        .join(Product, Product.id == SalesOrderItem.product_id)
        .where(SalesOrder.status.in_(analytics.VALID_SALES_STATUS))
        .where(func.date(SalesOrder.order_date) >= start)
    ) or 0
    revenue, cost = round(float(revenue), 2), round(float(cost), 2)
    profit = round(revenue - cost, 2)
    # 真实应收 = 已确认订单 − 已核销收款
    receivable = ar_service.receivable_total(db)
    return {
        "revenue": revenue,
        "cost": cost,
        "profit": profit,
        "margin": round(profit / revenue * 100, 1) if revenue else 0.0,
        "receivable": round(float(receivable), 2),
    }



# ---------------- 经营洞察 ----------------
def get_insights(db: Session, allowed_types: set[str] | None = None, owner: str | None = None) -> list[dict]:
    """owner 非空时，销售类洞察只统计数据范围内（本人）的数据。"""
    insights: list[dict] = []
    products = analytics.product_sales(db, 30, owner)
    sales_30 = sum(p["qty"] for p in products)
    total_revenue = sum(p["revenue"] for p in products)

    # 1. 库存预警（按可支撑天数排序）
    low = analytics.low_stock_products(db)
    for p in low[:5]:
        sold = next((x["qty"] for x in products if x["id"] == p.id), 0)
        daily = sold / 30 if sold else 0
        days_left = int(p.stock_qty / daily) if daily > 0 else None
        detail = f"{p.sku} {p.name} 当前库存 {p.stock_qty}{p.unit}，安全库存 {p.safety_stock}{p.unit}"
        if days_left is not None:
            detail += f"，按近 30 天日均销量约可支撑 {days_left} 天"
        else:
            detail += "，近 30 天无出库记录"
        insights.append(
            {
                "type": "stock",
                "level": "danger" if (days_left is not None and days_left <= 3) else "warning",
                "title": f"库存预警：{p.name}",
                "detail": detail,
                "action": "建议立即补货",
                "product_id": p.id,
                "sku": p.sku,
                "product_type": p.product_type,
            }
        )

    # 2. 滞销品
    dead = [p for p in db.scalars(select(Product).where(Product.is_active == True)).all()  # noqa: E712
            if p.id not in {x["id"] for x in products} and p.stock_qty > 0]
    if dead:
        value = sum(p.cost_price * p.stock_qty for p in dead)
        insights.append(
            {
                "type": "slow_moving",
                "level": "warning",
                "title": f"发现 {len(dead)} 个滞销商品",
                "detail": f"近 30 天零出库，占用库存资金约 {_money(value)}。代表商品："
                + "、".join(p.name for p in dead[:3]),
                "action": "考虑促销或停止补货",
            }
        )

    # 3. 销量趋势（近 15 天 vs 前 15 天）
    trend = analytics.daily_sales(db, 30, owner)
    recent = sum(x["amount"] for x in trend[-15:])
    before = sum(x["amount"] for x in trend[:15])
    if before > 0:
        change = round((recent - before) / before * 100, 1)
        if change <= -10:
            insights.append(
                {
                    "type": "trend",
                    "level": "warning",
                    "title": f"销售额环比下降 {abs(change)}%",
                    "detail": f"近 15 天销售额 {_money(recent)}，前 15 天 {_money(before)}",
                    "action": "建议排查大客户订单变化",
                }
            )
        elif change >= 10:
            insights.append(
                {
                    "type": "trend",
                    "level": "success",
                    "title": f"销售额环比增长 {change}%",
                    "detail": f"近 15 天销售额 {_money(recent)}，前 15 天 {_money(before)}",
                    "action": "关注库存是否可支撑",
                }
            )

    # 4. 客户流失风险
    customers = analytics.customer_stats(db, 30, owner)
    at_risk = [c for c in customers if c["last_order"] and (c["days_since_last"] or 0) >= 30]
    if at_risk:
        top = at_risk[0]
        insights.append(
            {
                "type": "customer",
                "level": "warning",
                "title": f"{len(at_risk)} 个客户 30 天未下单",
                "detail": f"如 {top['name']}（{top['level']} 级客户）已 {top['days_since_last']} 天无订单，"
                f"历史累计贡献 {_money(top['amount'])}",
                "action": "建议安排回访",
            }
        )

    # 5. 待处理单据
    draft_count = db.scalar(select(func.count()).select_from(SalesOrder).where(SalesOrder.status == "draft")) or 0
    if draft_count:
        insights.append(
            {
                "type": "workflow",
                "level": "info",
                "title": f"{draft_count} 张销售订单处于草稿状态",
                "detail": "草稿订单不会扣减库存，也不会计入销售统计",
                "action": "请及时确认或删除",
            }
        )

    # 6. 销量冠军
    if products:
        best = products[0]
        insights.append(
            {
                "type": "best_seller",
                "level": "success",
                "title": f"本月畅销商品：{best['name']}",
                "detail": f"近 30 天销售 {best['qty']}{'件'}，贡献营收 {_money(best['revenue'])}，"
                f"占整体销售额 {(best['revenue'] / total_revenue * 100) if total_revenue else 0:.1f}%",
                "action": "可考虑加大推广",
            }
        )

    # 7. 财务视角：毛利与应收
    finance = _finance_summary(db, 30)
    if finance["revenue"] > 0:
        insights.append(
            {
                "type": "finance",
                "level": "success" if finance["margin"] >= 25 else "warning",
                "title": f"近 30 天毛利率 {finance['margin']}%",
                "detail": f"营收 {_money(finance['revenue'])}，成本 {_money(finance['cost'])}，"
                f"毛利 {_money(finance['profit'])}，待收账款 {_money(finance['receivable'])}",
                "action": "关注毛利率变化与回款进度" if finance["margin"] >= 25 else "建议复核低价订单与采购成本",
            }
        )

    # 按角色可见范围过滤
    if allowed_types and "*" not in allowed_types:
        insights = [item for item in insights if item["type"] in allowed_types]
        if not insights:
            insights.append(
                {
                    "type": "system",
                    "level": "info",
                    "title": "当前无属于你职责范围的异常",
                    "detail": "系统暂未发现需要你处理的经营异常",
                    "action": "保持关注",
                }
            )

    if not insights:
        insights.append(
            {
                "type": "system",
                "level": "info",
                "title": "经营状况平稳",
                "detail": f"近 30 天共出库 {sales_30} 件，累计营收 {_money(total_revenue)}，暂未发现异常",
                "action": "保持关注",
            }
        )
    return insights


# ---------------- 销售预测 ----------------
def _linear_forecast(series: list[float], future: int) -> list[float]:
    n = len(series)
    if n < 2:
        return [series[-1] if series else 0.0] * future
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(series) / n
    denom = sum((x - mean_x) ** 2 for x in xs) or 1
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, series)) / denom
    intercept = mean_y - slope * mean_x
    result = []
    for i in range(1, future + 1):
        value = intercept + slope * (n - 1 + i)
        result.append(max(round(value, 2), 0.0))
    return result


def get_forecast(db: Session, product_id: int | None = None, days: int = 14, owner: str | None = None) -> dict:
    history_days = 30
    if product_id:
        product = db.get(Product, product_id)
        if not product:
            raise ValueError("商品不存在")
        stmt = (
            select(func.date(SalesOrder.order_date).label("day"), func.sum(SalesOrderItem.quantity))
            .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
            .where(SalesOrderItem.product_id == product_id)
            .where(SalesOrder.status.in_(analytics.VALID_SALES_STATUS))
        )
        if owner:
            stmt = stmt.where(SalesOrder.created_by == owner)
        rows = db.execute(stmt.group_by(func.date(SalesOrder.order_date))).all()
        start = (datetime.now() - timedelta(days=history_days - 1)).date()
        by_day = {str(d): float(q or 0) for d, q in rows}
        series = [by_day.get((start + timedelta(days=i)).isoformat(), 0.0) for i in range(history_days)]
        dates = [(start + timedelta(days=i)).isoformat() for i in range(history_days)]
        title = f"{product.name} 销量预测"
        unit = product.unit
    else:
        trend = analytics.daily_sales(db, history_days, owner)
        series = [t["amount"] for t in trend]
        dates = [t["date"] for t in trend]
        title = "整体销售额预测"
        unit = "元"

    predicted = _linear_forecast(series, days)
    today = datetime.now().date()
    future_dates = [(today + timedelta(days=i)).isoformat() for i in range(1, days + 1)]

    recent_avg = sum(series[-7:]) / 7 if series else 0
    forecast_avg = sum(predicted) / len(predicted) if predicted else 0
    change = round((forecast_avg - recent_avg) / recent_avg * 100, 1) if recent_avg else 0.0
    return {
        "title": title,
        "unit": unit,
        "history": [{"date": d, "value": round(v, 2)} for d, v in zip(dates, series)],
        "forecast": [{"date": d, "value": v} for d, v in zip(future_dates, predicted)],
        "summary": {
            "recent_daily_avg": round(recent_avg, 2),
            "forecast_daily_avg": round(forecast_avg, 2),
            "change_percent": change,
            "forecast_total": round(sum(predicted), 2),
            "trend": "上升" if change > 5 else ("下降" if change < -5 else "平稳"),
        },
    }


# ---------------- 智能问答 ----------------
def _deny(feature: str) -> dict:
    return {
        "answer": f"你的角色没有「{feature}」的查询权限。如需查看，请联系管理员调整角色权限。",
        "data": [],
    }


_RETURN_STATUS = {"draft": "草稿", "confirmed": "已确认", "cancelled": "已取消"}


def _return_overview(db: Session, owner: str | None) -> dict:
    """近 90 天退货概况；own 范围时只统计本人创建的单据。"""
    start = (datetime.now() - timedelta(days=89)).date().isoformat()
    overview = {}
    for kind, model in (("sales", SalesReturn), ("purchase", PurchaseReturn)):
        stmt = select(model).where(func.date(model.created_at) >= start)
        if owner:
            stmt = stmt.where(model.created_by == owner)
        records = db.scalars(stmt.order_by(model.id.desc())).all()
        confirmed = [row for row in records if row.status == "confirmed"]
        overview[kind] = {
            "count": len(records),
            "confirmed_count": len(confirmed),
            "confirmed_amount": round(sum(float(row.total_amount or 0) for row in confirmed), 2),
            "recent": [
                {
                    "return_no": row.return_no,
                    "order_no": row.order.order_no if row.order else "",
                    "partner": (
                        row.customer.name
                        if kind == "sales" and row.customer
                        else (row.supplier.name if row.supplier else "")
                    ),
                    "status": _RETURN_STATUS.get(row.status, row.status),
                    "total_amount": round(float(row.total_amount or 0), 2),
                }
                for row in records[:5]
            ],
        }
    return overview


def _rule_answer(db: Session, message: str, perms: set[str], role: str, owner: str | None = None) -> dict:
    text = message.strip()
    trend = analytics.daily_sales(db, 30, owner)
    products = analytics.product_sales(db, 30, owner)
    customers = analytics.customer_stats(db, 30, owner)

    if any(k in text for k in ("补货", "库存", "缺货", "备货")):
        if not ({"ai:replenish", "inventory:view"} & perms):
            return _deny("库存/补货")
        low = analytics.low_stock_products(db)
        if not low:
            return {"answer": f"当前没有低于安全库存的商品，库存健康。近 30 天累计营收 {_money(sum(t['amount'] for t in trend))}。",
                    "data": []}
        if "ai:replenish" not in perms:
            lines = [f"检测到 {len(low)} 个商品库存低于安全线："]
            for p in low[:8]:
                lines.append(f"· {p.name}（{p.sku}）库存 {p.stock_qty}{p.unit}，安全库存 {p.safety_stock}{p.unit}")
            return {"answer": "\n".join(lines) + "\n如需补货量测算，请使用采购角色的 AI 补货建议。", "data": []}
        suggestions = get_replenishment(db)[:5]
        lines = [f"检测到 {len(low)} 个商品库存低于安全线，建议补货Top5："]
        for i, s in enumerate(suggestions, 1):
            lines.append(
                f"{i}. {s['name']}（{s['sku']}）库存 {s['stock_qty']}{s['unit']}，"
                f"日均销 {s['avg_daily_sales']}，建议补 {s['suggested_qty']}{s['unit']}，"
                f"预计成本 {_money(s['estimated_cost'])}，供应商：{s['supplier_name']}"
            )
        return {"answer": "\n".join(lines), "data": suggestions}

    if any(k in text for k in ("应付", "付款", "欠供应商", "供应商对账")):
        if "payable:view" not in perms:
            return _deny("应付账款")
        items = ap_service.supplier_payables(db)
        total = ap_service.payable_total(db)
        if not items:
            return {"answer": f"当前应付账款已结清，剩余余额 {_money(total)}。", "data": []}
        lines = [f"当前应付账款合计 {_money(total)}，涉及 {len(items)} 家供应商："]
        for item in items[:5]:
            lines.append(
                f"· {item['supplier']}：未结 {_money(item['balance'])}，已付 {_money(item['paid'])}，"
                f"退货 {_money(item['returned'])}，账龄 {item['age_days']} 天"
            )
        return {"answer": "\n".join(lines), "data": items[:5]}

    if any(k in text for k in ("毛利", "成本", "利润", "应收", "账款", "回款")):
        if "finance:view" not in perms:
            return _deny("财务数据")
        f = _finance_summary(db, 30)
        return {
            "answer": (
                f"近 30 天营收 {_money(f['revenue'])}，成本 {_money(f['cost'])}，"
                f"毛利 {_money(f['profit'])}，毛利率 {f['margin']}%；"
                f"当前已确认订单待收款 {_money(f['receivable'])}。"
            ),
            "data": [{"指标": "营收", "金额": f["revenue"]}, {"指标": "成本", "金额": f["cost"]},
                     {"指标": "毛利", "金额": f["profit"]}, {"指标": "待收款", "金额": f["receivable"]}],
        }

    if any(k in text for k in ("预测", "趋势", "未来")):
        if "ai:forecast" not in perms:
            return _deny("销售预测")
        forecast = get_forecast(db, None, 14)
        s = forecast["summary"]
        return {
            "answer": (
                f"根据近 30 天数据，未来 14 天销售额预计合计 {_money(s['forecast_total'])}，"
                f"日均 {_money(s['forecast_daily_avg'])}，较最近 7 天日均"
                f"（{_money(s['recent_daily_avg'])}）{s['trend']} {abs(s['change_percent'])}%。"
            ),
            "data": forecast["forecast"],
        }

    if any(k in text for k in ("最好", "热销", "畅销", "卖得", "畅销品")):
        if not ({"sales:view", "dashboard:view"} & perms):
            return _deny("销售数据")
        if not products:
            return {"answer": "近 30 天还没有已确认的销售数据，先创建并确认销售订单后再问我。", "data": []}
        lines = ["近 30 天热销商品排行："]
        for i, p in enumerate(products[:5], 1):
            lines.append(f"{i}. {p['name']}：销量 {p['qty']}，销售额 {_money(p['revenue'])}")
        return {"answer": "\n".join(lines), "data": products[:5]}

    if any(k in text for k in ("客户", "会员", "大客户")):
        if "customer:view" not in perms:
            return _deny("客户数据")
        if not customers:
            return {"answer": "系统中还没有客户数据。", "data": []}
        lines = ["客户消费排行（近 30 天）："]
        for i, c in enumerate(customers[:5], 1):
            lines.append(
                f"{i}. {c['name']}（{c['level']}级）：{_money(c['amount'])}，{c['orders']} 单，"
                f"最近下单 {c['last_order'] or '无'}"
            )
        risk = [c for c in customers if (c["days_since_last"] or 0) >= 30 and c["last_order"]]
        if risk:
            lines.append(f"\n⚠️ {len(risk)} 个客户超过 30 天未下单，建议回访：" + "、".join(c["name"] for c in risk[:3]))
        return {"answer": "\n".join(lines), "data": customers[:5]}

    if any(k in text for k in ("退货", "退回", "红字")):
        if "return:view" not in perms:
            return _deny("退货数据")
        overview = _return_overview(db, owner)
        sales, purchase = overview["sales"], overview["purchase"]
        lines = [
            f"近 90 天销售退货 {sales['count']} 张（已确认 {sales['confirmed_count']} 张，"
            f"冲减应收 {_money(sales['confirmed_amount'])}）；"
            f"采购退货 {purchase['count']} 张（已确认 {purchase['confirmed_count']} 张，"
            f"冲减应付 {_money(purchase['confirmed_amount'])}）。"
        ]
        recent = sales["recent"] + purchase["recent"]
        if recent:
            lines.append("最近退货：")
            for row in recent[:5]:
                lines.append(
                    f"· {row['return_no']}（原单 {row['order_no'] or '-'}）"
                    f"{row['partner']} {_money(row['total_amount'])} {row['status']}"
                )
        return {"answer": "\n".join(lines), "data": recent[:5]}

    if any(k in text for k in ("盘点", "盘盈", "盘亏")):
        if "stocktake:view" not in perms:
            return _deny("库存盘点")
        orders = db.scalars(select(StocktakeOrder).order_by(StocktakeOrder.id.desc()).limit(10)).all()
        if not orders:
            return {"answer": "系统里还没有盘点单。", "data": []}
        pending = [order for order in orders if order.status == "draft"]
        diffs = [order for order in orders if any(item.diff_qty for item in order.items)]
        lines = [
            f"最近 {len(orders)} 张盘点单中，{len(pending)} 张未完成，{len(diffs)} 张存在账实差异："
        ]
        for order in diffs[:3]:
            detail = "、".join(
                f"{item.product.name if item.product else item.product_id}"
                f"（{'+' if item.diff_qty > 0 else ''}{item.diff_qty}）"
                for item in order.items
                if item.diff_qty
            )
            lines.append(
                f"· {order.stocktake_no}（{order.warehouse.name if order.warehouse else ''}）：{detail}"
            )
        data = [
            {
                "stocktake_no": order.stocktake_no,
                "warehouse": order.warehouse.name if order.warehouse else "",
                "status": order.status,
                "diff_count": sum(1 for item in order.items if item.diff_qty),
            }
            for order in orders
        ]
        return {"answer": "\n".join(lines), "data": data}

    if any(k in text for k in ("采购", "供应商", "进货")):
        if "purchase:view" not in perms:
            return _deny("采购数据")
        pending = analytics.pending_purchase(db)
        if not pending:
            return {"answer": "当前没有处于「已下单待入库」状态的采购单。", "data": []}
        lines = [f"有 {len(pending)} 张采购单待入库，合计 {_money(sum(p['total_amount'] for p in pending))}："]
        for p in pending[:5]:
            lines.append(f"{p['order_no']} - {p['supplier']} - {_money(p['total_amount'])} - {p['order_date']}")
        return {"answer": "\n".join(lines), "data": pending}

    if any(k in text for k in ("建议", "洞察", "分析", "异常", "问题")):
        if "ai:insight" not in perms:
            return _deny("经营洞察")
        insights = get_insights(db, insight_types_for(role), owner)
        lines = ["基于当前数据，为你生成以下经营建议："]
        for item in insights[:6]:
            lines.append(f"[{item['level'].upper()}] {item['title']}：{item['detail']} → {item['action']}")
        return {"answer": "\n".join(lines), "data": insights[:6]}

    if any(k in text for k in ("审批", "待批", "待审")):
        allowed = [
            kind
            for kind, perm in (("sales", "sales:approve"), ("purchase", "purchase:approve"))
            if perm in perms
        ]
        if not allowed:
            return _deny("订单审批")
        items = [row for row in approval_service.pending_orders(db) if row["order_type"] in allowed]
        if not items:
            return {"answer": "当前没有待审批的订单。", "data": []}
        total = sum(row["total_amount"] for row in items)
        lines = [f"当前有 {len(items)} 张订单待审批，合计 {_money(total)}："]
        for row in items[:5]:
            label = "销售" if row["order_type"] == "sales" else "采购"
            lines.append(
                f"· {row['order_no']}（{label}）{row['partner_name']} "
                f"{_money(row['total_amount'])}，提交人 {row['created_by']}"
            )
        return {"answer": "\n".join(lines), "data": items[:5]}

    if any(k in text for k in ("订单", "单据", "待办")):
        if not ({"sales:view", "purchase:view"} & perms):
            return _deny("订单数据")
        recent_stmt = select(SalesOrder)
        draft_stmt = select(func.count()).select_from(SalesOrder).where(SalesOrder.status == "draft")
        sales_pending_stmt = select(func.count()).select_from(SalesOrder).where(
            SalesOrder.status == "pending"
        )
        if owner:
            recent_stmt = recent_stmt.where(SalesOrder.created_by == owner)
            draft_stmt = draft_stmt.where(SalesOrder.created_by == owner)
            sales_pending_stmt = sales_pending_stmt.where(SalesOrder.created_by == owner)
        recent = db.scalars(recent_stmt.order_by(SalesOrder.id.desc()).limit(5)).all()
        draft = db.scalar(draft_stmt) or 0
        sales_pending = db.scalar(sales_pending_stmt) or 0
        purchase_pending = db.scalar(
            select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.status == "pending")
        ) or 0
        lines = [
            f"当前有 {draft} 张草稿销售订单待确认，"
            f"{sales_pending} 张销售订单、{purchase_pending} 张采购订单待审批。最近订单："
        ]
        for o in recent:
            lines.append(
                f"{o.order_no} - {o.customer.name if o.customer else ''} - "
                f"{_money(o.total_amount)} - {o.status}"
            )
        return {"answer": "\n".join(lines), "data": [
            {"order_no": o.order_no, "customer": o.customer.name if o.customer else "",
             "amount": o.total_amount, "status": o.status} for o in recent
        ]}

    # 默认：经营总览（按权限过滤提示语）
    total_amount = sum(t["amount"] for t in trend)
    total_orders = sum(t["orders"] for t in trend)
    low = analytics.low_stock_products(db)
    tips = []
    if "ai:replenish" in perms:
        tips.append("· 哪些商品需要补货？")
    if {"sales:view", "dashboard:view"} & perms:
        tips.append("· 最近卖得最好的商品是什么？")
    if "ai:forecast" in perms:
        tips.append("· 帮我预测下未来两周的销售")
    if "customer:view" in perms:
        tips.append("· 有哪些客户可能流失？")
    if "finance:view" in perms:
        tips.append("· 近 30 天毛利和回款情况如何？")
    if "ai:insight" in perms:
        tips.append("· 给我一些经营建议")
    head = f"近 30 天销售额 {_money(total_amount)}，共 {total_orders} 笔订单"
    if "inventory:view" in perms:
        head += f"，库存总值 {_money(analytics.inventory_value(db))}，{len(low)} 个商品存在库存预警"
    return {
        "answer": head + "。\n" + ("你可以这样问我：\n" + "\n".join(tips) if tips else "请使用左侧菜单进入你的业务模块。"),
        "data": [],
    }


_http_client: httpx.Client | None = None


def _client() -> httpx.Client:
    """复用同一个连接池：每轮对话都新建 TLS 连接会明显拉高首字延迟。"""
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(
            timeout=httpx.Timeout(settings.LLM_TIMEOUT_SECONDS),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _http_client


def _tool_specs(role: str) -> list[dict]:
    """把已注册工具转成 OpenAI tools 定义，只暴露该角色有权使用的能力。"""
    specs = []
    for tool in available_tools(role):
        schema = tool.params.model_json_schema()
        schema.pop("title", None)
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": schema,
                },
            }
        )
    return specs


def _post_chat(messages: list[dict], tools: list[dict], tool_choice: str = "auto") -> dict | None:
    """调用兼容 OpenAI 协议的 /chat/completions。

    429/5xx 与连接错误重试一次；仍失败则返回 None，由调用方决定降级。
    """
    payload: dict = {
        "model": settings.LLM_MODEL,
        "messages": messages,
        "temperature": settings.LLM_TEMPERATURE,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice

    url = f"{settings.LLM_BASE_URL.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.LLM_API_KEY}"}
    last_error: str = "未知错误"
    for attempt in range(2):
        started = time.perf_counter()
        try:
            response = _client().post(url, headers=headers, json=payload)
            if response.status_code in (429, 502, 503, 504) and attempt == 0:
                last_error = f"HTTP {response.status_code}"
                time.sleep(0.8)
                continue
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == 0:
                time.sleep(0.5)
                continue
            break

        usage = data.get("usage") or {}
        logger.info(
            "大模型调用完成：%.2fs，prompt_tokens=%s，completion_tokens=%s，携带工具 %s 个",
            time.perf_counter() - started,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            len(tools),
        )
        return data

    logger.warning("大模型调用失败，降级到规则引擎：%s", last_error)
    return None


def _dump(value) -> str:
    """统一序列化参数，保证「算长度」和「真正发给模型」用的是同一份文本。"""
    return json.dumps(value, ensure_ascii=False, default=str)


def _fit_rows(build, rows: list, limit: int) -> dict | None:
    """二分出能放进 limit 的最大行数；一行都放不下时返回 None。"""
    low, high, best = 1, len(rows), None
    while low <= high:
        mid = (low + high) // 2
        candidate = build(rows[:mid])
        if len(_dump(candidate)) <= limit:
            best, low = candidate, mid + 1
        else:
            high = mid - 1
    return best


def _tool_content(body: dict, limit: int | None = None) -> str:
    """把工具结果压成模型可读的 JSON。

    超长时按行裁剪并标注 truncated。直接按字符截断会把 JSON 切成半截，
    模型解析失败或读到残缺数字，正是「答非所问」的常见来源。
    """
    limit = limit or settings.LLM_TOOL_RESULT_CHARS
    text = _dump(body)
    if len(text) <= limit:
        return text

    data = body.get("data")
    if isinstance(data, dict):
        for key in ("rows", "items", "recent_orders", "suggestions"):
            rows = data.get(key)
            if not isinstance(rows, list) or not rows:
                continue

            def build(part: list, key: str = key, data: dict = data) -> dict:
                return {
                    **body,
                    "truncated": True,
                    "note": f"结果过长，只保留前 {len(part)} 条",
                    "data": {**data, key: part},
                }

            fitted = _fit_rows(build, rows, limit)
            if fitted:
                return _dump(fitted)
    elif isinstance(data, list) and data:
        def build_list(part: list) -> dict:
            return {
                **body,
                "truncated": True,
                "note": f"结果过长，只保留前 {len(part)} 条",
                "data": part,
            }

        fitted = _fit_rows(build_list, data, limit)
        if fitted:
            return _dump(fitted)

    # 单行就超预算：退化成预览文本，仍然是合法 JSON，模型不会拿到残缺结构
    return _dump(
        {
            "ok": body.get("ok", True),
            "truncated": True,
            "note": "结果过长，仅保留开头片段，请缩小查询范围",
            "preview": text[: max(limit - 160, 200)],
        }
    )


def _parse_tool_args(call: dict) -> tuple[dict | None, str]:
    """解析模型给的参数；解析失败要明确报错，不能当成空参数去查一份错数据。"""
    raw = (call.get("function") or {}).get("arguments") or "{}"
    try:
        args = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"参数不是合法 JSON：{exc.msg}"
    if not isinstance(args, dict):
        return None, "参数必须是一个 JSON 对象"
    return args, ""


def _execute_tool_call(
    call: dict, ctx: ToolContext, cache: dict[str, ToolResult]
) -> tuple[dict, list[dict], str, dict | None]:
    """执行一个 tool_call，返回 (tool 消息, 可展示明细, 工具名, 原始数据)。

    同一轮对话里参数完全相同的调用只真正执行一次：模型偶尔会把同一个查询调两遍，
    重复执行既浪费一次数据库查询，也会在审计日志里留下重复记录。
    """
    name = ((call.get("function") or {}).get("name") or "").strip()
    args, parse_error = _parse_tool_args(call)
    if args is None:
        body = {"ok": False, "error": "invalid_args", "detail": parse_error}
        return {
            "role": "tool",
            "tool_call_id": call.get("id", ""),
            "content": _tool_content(body),
        }, [], name, None

    key = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
    if key not in cache:
        cache[key] = execute(name, args, ctx)
    result = cache[key]

    rows: list[dict] = []
    payload = result.data if isinstance(result.data, dict) else None
    if result.ok:
        body = {"ok": True, "data": result.data}
        if isinstance(result.data, dict):
            for field in ("rows", "items", "recent_orders"):
                if isinstance(result.data.get(field), list):
                    rows = result.data[field]
                    break
        elif isinstance(result.data, list):
            rows = result.data
    else:
        body = {"ok": False, "error": result.error, "detail": result.error_detail}

    message = {"role": "tool", "tool_call_id": call.get("id", ""), "content": _tool_content(body)}
    return message, rows[:20], name, payload


def _system_prompt(role: str, owner: str | None) -> str:
    """构造系统提示词：角色、当前时间、统计口径与回答格式都在这里约定。"""
    meta = role_meta(role)
    now = datetime.now()
    weekday = "一二三四五六日"[now.weekday()]
    scope_note = (
        f"仅本人负责的数据（{owner}），工具已按此过滤，不要推测范围外的客户或订单。"
        if owner
        else "全部数据。"
    )
    return (
        "你是「AI-ERP」系统的经营分析助手，只能依据本系统的真实业务数据回答。\n"
        f"当前时间：{now:%Y-%m-%d}（星期{weekday}）。用户说「本月」「上周」「最近三天」时，"
        "先换算成工具参数里的天数再调用。\n"
        f"提问人：{meta['name']}（{meta['description']}）；数据范围：{scope_note}\n"
        "统计口径：销售金额与订单数只统计「已确认」「已完成」订单，草稿与已取消不计；"
        "库存是现存量；在途是已下单未入库的采购数量；应收按已确认未完成订单估算。\n"
        "工作规则：\n"
        "1. 问题涉及具体数字、名单、排名或时间范围时，必须先调用工具取数；一次不够可以继续调用，"
        "但不要用相同参数重复调用。\n"
        "2. 只能使用工具返回的数据作答，禁止编造或估算工具没给出的数字；工具返回 ok=false 时，"
        "如实说明原因（无权限/参数不合法/没有数据），并告诉用户可以在哪个页面自查。\n"
        "3. 你只有只读能力与开单预览：不能建单、改单、审批、删除；用户要写操作时，"
        "说明需要到对应页面操作，开单可以先用 propose_sales_order 生成待确认预览。\n"
        "4. 与业务数据无关的问题、或只是问「你能做什么」时，直接简短回答，不必调用工具。\n"
        "回答要求：中文；先给结论再给依据；关键数字带单位和统计范围；最多 6 行；"
        "不要 Markdown 表格、标题或代码块。\n"
    )


def _llm_answer(
    db: Session,
    message: str,
    history: list[dict],
    role: str,
    user,
    owner: str | None = None,
) -> dict | None:
    """带工具调用的大模型回答；未配置密钥、模型失败或最终无内容时返回 None 交给规则引擎。

    轮次或调用次数用尽时不直接放弃，而是追加一条「禁止再调工具」的指令，让模型基于已取到的
    数据收口；否则一个已经查到一半的问题会被甩给规则引擎，答非所问。
    """
    if not settings.llm_enabled:
        return None

    messages: list[dict] = [{"role": "system", "content": _system_prompt(role, owner)}]
    history_window = settings.LLM_MAX_HISTORY
    for item in (history[-history_window:] if history_window else []):
        item_role = item.get("role")
        content = (item.get("content") or "").strip()
        if item_role in ("user", "assistant") and content:
            messages.append({"role": item_role, "content": content[:2000]})
    messages.append({"role": "user", "content": message})

    specs = _tool_specs(role)
    ctx = ToolContext(db=db, user=user, owner=owner)
    cache: dict[str, ToolResult] = {}
    used: list[str] = []
    table: list[dict] = []
    proposal: dict | None = None
    executed = 0

    for _round in range(settings.LLM_MAX_TOOL_ROUNDS):
        data = _post_chat(messages, specs)
        if data is None:
            return None
        reply = ((data.get("choices") or [{}])[0]).get("message") or {}
        calls = reply.get("tool_calls") or []
        if not calls:
            answer = (reply.get("content") or "").strip()
            if not answer:
                return None
            return {
                "answer": answer,
                "data": table,
                "source": "llm",
                "tools_used": used,
                "draft": proposal,
            }

        messages.append(
            {"role": "assistant", "content": reply.get("content") or "", "tool_calls": calls}
        )
        for call in calls:
            tool_message, rows, name, payload = _execute_tool_call(call, ctx, cache)
            messages.append(tool_message)
            if name and name not in used:
                used.append(name)
            executed += 1
            if rows:
                table = rows
            # 开单预览：前端据此渲染「确认建单」卡片，模型无法自行提交
            if isinstance(payload, dict) and payload.get("draft"):
                proposal = payload
            if executed >= settings.LLM_MAX_TOOL_CALLS:
                break
        if executed >= settings.LLM_MAX_TOOL_CALLS:
            break

    messages.append(
        {
            "role": "user",
            "content": "工具调用次数已用完。请只根据上面已经返回的数据直接回答，不要再调用工具；"
            "数据不足就说明缺少哪项数据。",
        }
    )
    data = _post_chat(messages, specs, "none")
    if data is None:
        return None
    reply = ((data.get("choices") or [{}])[0]).get("message") or {}
    answer = (reply.get("content") or "").strip()
    if not answer:
        return None
    return {"answer": answer, "data": table, "source": "llm", "tools_used": used, "draft": proposal}


def chat(db: Session, message: str, history: list[dict] | None = None, user=None) -> dict:
    role = getattr(user, "role", "sales")
    perms = set(permissions_for(role))
    if not message.strip():
        return {"answer": "请输入你想咨询的内容。", "source": "rule", "data": []}
    if "ai:assist" not in perms:
        return {
            "answer": "你的角色未开通 AI 问答权限，请联系管理员调整角色。",
            "source": "rule",
            "data": [],
        }
    llm_result = _llm_answer(db, message[:2000], history or [], role, user, scope_owner(user))
    if llm_result:
        return llm_result
    result = _rule_answer(db, message, perms, role, scope_owner(user))
    return {"answer": result["answer"], "source": "rule", "data": result["data"]}
