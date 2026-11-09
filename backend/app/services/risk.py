"""风险雷达：把散落在各模块的异常汇总成一张可行动的清单。

每一条风险都带一个可直接跳转的动作（页面 + 查询参数），
目的是让人从「我要去哪个页面找问题」变成「点一下就去处理」。
"""
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Customer, Product, PurchaseOrder, SalesOrder
from ..permissions import has_permission
from ..scoping import scope_owner
from . import analytics, approvals, credit, receivables

LEVEL_ORDER = {"danger": 0, "warning": 1, "info": 2}
CHURN_DAYS = 45
STANDARD_LIMIT = 12


def _item(
    key: str,
    level: str,
    title: str,
    detail: str,
    *,
    metric: float = 0.0,
    metric_label: str = "",
    action: dict | None = None,
) -> dict:
    return {
        "key": key,
        "level": level,
        "title": title,
        "detail": detail,
        "metric": round(float(metric or 0), 2),
        "metric_label": metric_label,
        "action": action or {},
    }


def _group(key: str, title: str, icon: str, items: list[dict]) -> dict:
    items = sorted(items, key=lambda row: (LEVEL_ORDER.get(row["level"], 9), -row["metric"]))
    return {
        "key": key,
        "title": title,
        "icon": icon,
        "count": len(items),
        "danger": sum(1 for row in items if row["level"] == "danger"),
        "warning": sum(1 for row in items if row["level"] == "warning"),
        "items": items[:STANDARD_LIMIT],
    }


def _can(user, *permissions: str) -> bool:
    return any(has_permission(getattr(user, "role", ""), perm) for perm in permissions)


def _credit_group(db: Session, owner: str | None) -> dict:
    """信用风险：超额或使用率 ≥ 80% 的客户。"""
    stmt = select(Customer).where(Customer.credit_limit > 0)
    if owner:
        stmt = stmt.where(Customer.owner == owner)
    items = []
    for customer in db.scalars(stmt).all():
        info = credit.summary(db, customer)
        if not info["enabled"]:
            continue
        if info["over_limit"]:
            level = "danger"
        elif info["usage_percent"] >= 80:
            level = "warning"
        else:
            continue
        state = "已超限" if info["over_limit"] else "接近上限"
        items.append(
            _item(
                f"credit-{customer.id}",
                level,
                f"{customer.name} 信用额度{state}",
                f"额度 {info['credit_limit']:,.2f}，已占用 {info['outstanding']:,.2f}，"
                f"可用 {max(info['available'], 0):,.2f}",
                metric=info["usage_percent"],
                metric_label=f"使用率 {info['usage_percent']}%",
                action={"label": "查看客户", "path": "/customers", "query": {"keyword": customer.name}},
            )
        )
    return _group("credit", "信用风险", "Wallet", items)


def _stock_group(db: Session, owner: str | None) -> dict:
    """库存风险：已缺货与低于安全线，附带可支撑天数。"""
    sold = {row["id"]: row["qty"] for row in analytics.product_sales(db, 30, owner)}
    products = db.scalars(
        select(Product)
        .where(Product.is_active == True)  # noqa: E712
        .where(Product.stock_qty <= Product.safety_stock)
        .order_by((Product.stock_qty - Product.safety_stock), Product.id)
    ).all()
    items = []
    for product in products:
        daily = sold.get(product.id, 0) / 30
        cover = int(product.stock_qty / daily) if daily > 0 else None
        if product.stock_qty <= 0:
            level, state = "danger", "已缺货"
        elif cover is not None and cover <= 3:
            level, state = "danger", f"仅可支撑 {cover} 天"
        else:
            level, state = "warning", "低于安全库存"
        detail = f"{product.sku} 库存 {product.stock_qty}{product.unit}，安全库存 {product.safety_stock}{product.unit}"
        if cover is not None:
            detail += f"，按近 30 天日均销量可支撑 {cover} 天"
        else:
            detail += "，近 30 天无出库"
        items.append(
            _item(
                f"stock-{product.id}",
                level,
                f"{product.name} {state}",
                detail,
                metric=max(product.safety_stock - product.stock_qty, 0),
                metric_label=f"缺口 {max(product.safety_stock - product.stock_qty, 0)}{product.unit}",
                action={"label": "查看库存", "path": "/inventory", "query": {"keyword": product.sku}},
            )
        )
    return _group("stock", "库存风险", "Box", items)


def _slow_group(db: Session, owner: str | None) -> dict:
    """滞销风险：30 天零出库但仍有库存，按占用资金排序。"""
    sold_ids = {row["id"] for row in analytics.product_sales(db, 30, owner)}
    products = db.scalars(
        select(Product)
        .where(Product.is_active == True)  # noqa: E712
        .where(Product.stock_qty > 0)
        .order_by(Product.id)
    ).all()
    dead = [p for p in products if p.id not in sold_ids]
    dead.sort(key=lambda p: p.cost_price * p.stock_qty, reverse=True)
    items = []
    for product in dead[:STANDARD_LIMIT]:
        capital = round(float(product.cost_price or 0) * product.stock_qty, 2)
        items.append(
            _item(
                f"slow-{product.id}",
                "warning",
                f"{product.name} 近 30 天零出库",
                f"{product.sku} 库存 {product.stock_qty}{product.unit}，占用资金 {capital:,.2f}",
                metric=capital,
                metric_label=f"占用 {capital:,.2f}",
                action={"label": "查看商品", "path": "/products", "query": {"keyword": product.sku}},
            )
        )
    group = _group("slow", "滞销与资金占用", "TrendCharts", items)
    group["total_capital"] = round(sum(float(p.cost_price or 0) * p.stock_qty for p in dead), 2)
    return group


def _ar_group(db: Session) -> dict:
    """应收风险：逾期越久级别越高。"""
    items = []
    for row in receivables.customer_receivables(db):
        if row["age_days"] <= 30:
            continue
        level = "danger" if row["age_days"] > 60 else "warning"
        items.append(
            _item(
                f"ar-{row['customer_id']}",
                level,
                f"{row['customer']} 应收逾期 {row['age_days']} 天",
                f"未结 {row['balance']:,.2f}，共 {row['orders']} 单（{row['bucket']}）",
                metric=row["balance"],
                metric_label=f"¥{row['balance']:,.2f}",
                action={"label": "去对账", "path": "/receivables", "query": {"keyword": row["customer"]}},
            )
        )
    return _group("receivable", "应收逾期", "Tickets", items)


def _churn_group(db: Session, owner: str | None) -> dict:
    """客户流失风险：超过阈値未下单，按历史贡献排序。"""
    items = []
    for row in analytics.customer_stats(db, 90, owner):
        if not row["last_order"]:
            continue
        if (row["days_since_last"] or 0) < CHURN_DAYS:
            continue
        items.append(
            _item(
                f"churn-{row['id']}",
                "warning",
                f"{row['name']} 已 {row['days_since_last']} 天未下单",
                f"{row['level']} 级客户，累计成交 {row['amount']:,.2f}，最近下单 {row['last_order']}",
                metric=row["amount"],
                metric_label=f"累计 ¥{row['amount']:,.2f}",
                action={"label": "查看客户", "path": "/customers", "query": {"keyword": row["name"]}},
            )
        )
    return _group("churn", "客户流失预警", "User", items)


def _flow_group(db: Session, user) -> dict:
    """待办风险：待审批订单与挂着不动的草稿单。"""
    items = []
    allowed = [
        kind
        for kind, perm in (("sales", "sales:approve"), ("purchase", "purchase:approve"))
        if has_permission(getattr(user, "role", ""), perm)
    ]
    if allowed:
        for row in approvals.pending_orders(db):
            if row["order_type"] not in allowed:
                continue
            label = "销售" if row["order_type"] == "sales" else "采购"
            items.append(
                _item(
                    f"approve-{row['order_type']}-{row['order_id']}",
                    "warning",
                    f"{label}订单 {row['order_no']} 待审批",
                    f"{row['partner_name']} · {row['total_amount']:,.2f} · 提交人 {row['created_by']}",
                    metric=row["total_amount"],
                    metric_label=f"¥{row['total_amount']:,.2f}",
                    action={"label": "去审批", "path": "/approvals", "query": {}},
                )
            )

    if has_permission(getattr(user, "role", ""), "sales:manage"):
        drafts = db.scalar(
            select(func.count()).select_from(SalesOrder).where(SalesOrder.status == "draft")
        ) or 0
        if drafts:
            items.append(
                _item(
                    "draft-sales",
                    "info",
                    f"{drafts} 张销售订单停在草稿",
                    "草稿不扣库存也不计入销售统计，确认后才会生效",
                    metric=drafts,
                    metric_label=f"{drafts} 张",
                    action={"label": "去处理", "path": "/sales-orders", "query": {"status": "draft"}},
                )
            )
    if has_permission(getattr(user, "role", ""), "purchase:manage"):
        drafts = db.scalar(
            select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.status == "draft")
        ) or 0
        if drafts:
            items.append(
                _item(
                    "draft-purchase",
                    "info",
                    f"{drafts} 张采购订单停在草稿",
                    "草稿采购单不会进入在途量，补货测算会重复计算需求",
                    metric=drafts,
                    metric_label=f"{drafts} 张",
                    action={"label": "去处理", "path": "/purchase-orders", "query": {"status": "draft"}},
                )
            )
    return _group("flow", "待办与流程", "CircleCheck", items)


def build(db: Session, user) -> dict:
    """按角色权限组装风险清单；没有权限的板块直接不出现，而不是返回空壳。"""
    owner = scope_owner(user)
    groups = []
    if _can(user, "finance:view", "sales:view", "customer:view"):
        groups.append(_credit_group(db, owner))
    if _can(user, "inventory:view"):
        groups.append(_stock_group(db, owner))
        groups.append(_slow_group(db, owner))
    if _can(user, "finance:view"):
        groups.append(_ar_group(db))
    if _can(user, "customer:view"):
        groups.append(_churn_group(db, owner))
    if _can(user, "sales:approve", "purchase:approve", "sales:manage", "purchase:manage"):
        groups.append(_flow_group(db, user))

    groups = [group for group in groups if group["count"]]
    total = sum(group["count"] for group in groups)
    danger = sum(group["danger"] for group in groups)
    warning = sum(group["warning"] for group in groups)
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "total": total,
            "danger": danger,
            "warning": warning,
            "info": total - danger - warning,
            "groups": len(groups),
        },
        "groups": groups,
    }
