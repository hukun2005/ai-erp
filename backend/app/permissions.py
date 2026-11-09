"""角色与权限定义（RBAC）。

权限采用 `资源:动作` 形式，前端菜单、按钮和后端接口共用同一套 key。
`data_scope` 表示数据范围：all=全部数据，own=仅本人负责的数据。
"""

ALL_PERMISSIONS = [
    "dashboard:view",
    "product:view",
    "product:manage",
    "inventory:view",
    "inventory:adjust",
    "transfer:view",
    "transfer:manage",
    "warehouse:manage",
    "customer:view",
    "customer:manage",
    "supplier:view",
    "supplier:manage",
    "sales:view",
    "sales:manage",
    "sales:approve",
    "purchase:view",
    "purchase:manage",
    "purchase:receive",
    "purchase:approve",
    "outbound:approve",
    "return:approve",
    "payable:approve",
    "requisition:approve",
    "stocktake:approve",
    "return:view",
    "return:manage",
    "stocktake:view",
    "stocktake:manage",
    "inbound:view",
    "inbound:manage",
    "outbound:view",
    "outbound:manage",
    "bom:view",
    "bom:manage",
    "production:view",
    "production:manage",
    "quote:view",
    "quote:manage",
    "requisition:view",
    "requisition:manage",
    "report:view",
    "template:view",
    "template:manage",
    "finance:view",
    "finance:manage",
    "ledger:view",
    "ledger:manage",
    "payable:view",
    "payable:manage",
    "invoice:view",
    "invoice:manage",
    "bank:view",
    "bank:manage",
    "period:manage",
    "backup:manage",
    "ai:assist",
    "ai:insight",
    "ai:replenish",
    "ai:forecast",
    "user:manage",
    "audit:view",
    "notify:manage",
    "automation:view",
    "automation:manage",
    "approval:manage",
    "credit:manage",
]

ROLE_DEFINITIONS: dict[str, dict] = {
    "admin": {
        "name": "管理员",
        "color": "#6366f1",
        "description": "系统全部模块与用户管理权限",
        "permissions": ["*"],
        "data_scope": "all",
        "insight_types": ["*"],
        "ai_prompts": [
            "给我一些经营建议",
            "哪些商品需要补货？",
            "帮我预测下未来两周的销售",
            "有哪些客户可能流失？",
            "还有多少应付账款没付？",
            "最近的盘点有差异吗？",
        ],
    },
    "sales": {
        "name": "销售员",
        "color": "#0ea5e9",
        "description": "负责客户跟进与销售订单，可查看商品和库存余量",
        "permissions": [
            "dashboard:view",
            "product:view",
            "customer:view",
            "customer:manage",
            "sales:view",
            "sales:manage",
            "return:view",
            "return:manage",
            "quote:view",
            "quote:manage",
            "bom:view",
            "production:view",
            "transfer:view",
            "outbound:view",
            "template:view",
            "ai:assist",
            "ai:insight",
            "ai:forecast",
        ],
        "data_scope": "own",
        "insight_types": ["trend", "best_seller", "customer", "workflow"],
        "ai_prompts": [
            "最近卖得最好的商品是什么？",
            "有哪些客户可能流失？",
            "帮我预测下未来两周的销售",
            "现在还有哪些待确认的订单？",
            "最近有哪些销售退货？",
        ],
    },
    "purchaser": {
        "name": "采购员",
        "color": "#f59e0b",
        "description": "负责供应商与采购订单，AI 主动给出补货建议",
        "permissions": [
            "dashboard:view",
            "product:view",
            "supplier:view",
            "supplier:manage",
            "purchase:view",
            "purchase:manage",
            "return:view",
            "return:manage",
            "requisition:view",
            "requisition:manage",
            "requisition:approve",
            "bom:view",
            "production:view",
            "report:view",
            "transfer:view",
            "inbound:view",
            "outbound:view",
            "template:view",
            "ai:assist",
            "ai:insight",
            "ai:replenish",
            "ai:forecast",
        ],
        "data_scope": "own",
        "insight_types": ["stock", "slow_moving", "trend", "best_seller"],
        "ai_prompts": [
            "哪些商品需要补货？",
            "帮我生成补货方案",
            "哪些商品属于滞销库存？",
            "现在有哪些采购单待入库？",
            "最近有哪些采购退货？",
        ],
    },
    "warehouse": {
        "name": "仓管员",
        "color": "#10b981",
        "description": "负责库存盘点、出入库与采购入库确认",
        "permissions": [
            "dashboard:view",
            "product:view",
            "inventory:view",
            "inventory:adjust",
            "transfer:view",
            "transfer:manage",
            "purchase:view",
            "purchase:receive",
            "return:view",
            "return:manage",
            "stocktake:view",
            "stocktake:manage",
            "bom:view",
            "production:view",
            "production:manage",
            "report:view",
            "inbound:view",
            "inbound:manage",
            "outbound:view",
            "outbound:manage",
            "template:view",
            "ai:assist",
            "ai:insight",
        ],
        "data_scope": "all",
        "insight_types": ["stock", "slow_moving", "workflow"],
        "ai_prompts": [
            "哪些商品库存不足？",
            "哪些商品积压卖不动？",
            "哪些采购单还没有入库？",
            "最近的盘点差异有哪些？",
        ],
    },
    "finance": {
        "name": "财务",
        "color": "#8b5cf6",
        "description": "负责营收成本核算、毛利与应收分析",
        "permissions": [
            "dashboard:view",
            "finance:view",
            "finance:manage",
            "product:view",
            "customer:view",
            "supplier:view",
            "sales:view",
            "sales:approve",
            "purchase:view",
            "purchase:approve",
            "outbound:approve",
            "return:approve",
            "payable:approve",
            "requisition:approve",
            "stocktake:approve",
            "requisition:view",
            "bom:view",
            "production:view",
            "report:view",
            "return:view",
            "inbound:view",
            "outbound:view",
            "template:view",
            "payable:view",
            "payable:manage",
            "invoice:view",
            "invoice:manage",
            "bank:view",
            "bank:manage",
            "period:manage",
            "ledger:view",
            "ledger:manage",
            "ai:assist",
            "ai:insight",
            "ai:forecast",
        ],
        "data_scope": "all",
        "insight_types": ["finance", "trend", "best_seller"],
        "ai_prompts": [
            "这个月的毛利情况怎么样？",
            "哪些客户还有应收未结？",
            "帮我预测下未来两周的销售额",
            "还有多少应付账款没付？",
            "给我一些经营建议",
        ],
    },
}


def permissions_for(role: str) -> list[str]:
    definition = ROLE_DEFINITIONS.get(role)
    if not definition:
        return []
    perms = definition["permissions"]
    return ALL_PERMISSIONS.copy() if "*" in perms else list(perms)


def has_permission(role: str, permission: str) -> bool:
    if role not in ROLE_DEFINITIONS:
        return False
    perms = ROLE_DEFINITIONS[role]["permissions"]
    return "*" in perms or permission in perms


def can_view_cost(role: str) -> bool:
    """字段级权限：成本价/毛利属敏感字段，仅财务/采购/库存相关角色可见。

    销售角色即便能看商品与订单，也不应看到成本价；采购、仓管、财务、管理、超管可见。
    """
    return any(has_permission(role, p) for p in ("finance:view", "purchase:view", "inventory:view"))


def insight_types_for(role: str) -> set[str]:
    definition = ROLE_DEFINITIONS.get(role, {})
    types = definition.get("insight_types", [])
    if "*" in types:
        return {"*"}
    return set(types)


def data_scope(role: str) -> str:
    """all=可见全部数据；own=仅可见本人负责的数据。"""
    return ROLE_DEFINITIONS.get(role, {}).get("data_scope", "all")


def owns_own_scope(role: str) -> bool:
    return data_scope(role) == "own"


def role_meta(role: str) -> dict:
    definition = ROLE_DEFINITIONS.get(role, {})
    scope = data_scope(role)
    return {
        "role": role,
        "name": definition.get("name", "未知角色"),
        "color": definition.get("color", "#6b7280"),
        "description": definition.get("description", ""),
        "permissions": permissions_for(role),
        "data_scope": scope,
        "data_scope_name": "仅本人数据" if scope == "own" else "全部数据",
        "ai_prompts": definition.get("ai_prompts", []),
    }
