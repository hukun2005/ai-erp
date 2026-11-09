"""行业审批模板：一键写入常见的多级审批规则。

模板只预置「单据类型 + 金额区间 + 级别 + 所需权限」，
不写死具体审批人（人名/账号因公司而异），套用后再按需在界面上调整。
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import ApprovalRule

# 每个模板：key / 名称 / 适用说明 / 规则列表
TEMPLATES: list[dict] = [
    {
        "key": "small",
        "name": "小微企业（单级审批）",
        "desc": "销售、采购各 1 级，负责人审完即生效。适合老板直接管单的场景。",
        "doc_types": ["sales", "purchase"],
        "rules": [
            {"doc_type": "sales", "level": 1, "min_amount": 0, "permission": "sales:approve", "level_name": "负责人审批"},
            {"doc_type": "purchase", "level": 1, "min_amount": 0, "permission": "purchase:approve", "level_name": "负责人审批"},
        ],
    },
    {
        "key": "trade",
        "name": "贸易公司（金额分级）",
        "desc": "1 万元以下主管审批；1 万元以上加一级财务复核。",
        "doc_types": ["sales", "purchase"],
        "rules": [
            {"doc_type": "sales", "level": 1, "min_amount": 0, "permission": "sales:approve", "level_name": "业务主管"},
            {"doc_type": "sales", "level": 2, "min_amount": 10000, "permission": "finance:manage", "level_name": "财务复核"},
            {"doc_type": "purchase", "level": 1, "min_amount": 0, "permission": "purchase:approve", "level_name": "采购主管"},
            {"doc_type": "purchase", "level": 2, "min_amount": 10000, "permission": "finance:manage", "level_name": "财务复核"},
        ],
    },
    {
        "key": "manufacture",
        "name": "制造业（订单+出库+盘点+申请）",
        "desc": "订单 5 万元分级；出库、盘点差异需仓库主管审批；采购申请 5000 元以上加财务。",
        "doc_types": ["sales", "purchase", "outbound", "stocktake", "requisition"],
        "rules": [
            {"doc_type": "sales", "level": 1, "min_amount": 0, "permission": "sales:approve", "level_name": "业务主管"},
            {"doc_type": "sales", "level": 2, "min_amount": 50000, "permission": "finance:manage", "level_name": "财务复核"},
            {"doc_type": "purchase", "level": 1, "min_amount": 0, "permission": "purchase:approve", "level_name": "采购主管"},
            {"doc_type": "purchase", "level": 2, "min_amount": 50000, "permission": "finance:manage", "level_name": "财务复核"},
            {"doc_type": "outbound", "level": 1, "min_amount": 0, "permission": "outbound:approve", "level_name": "仓库主管"},
            {"doc_type": "stocktake", "level": 1, "min_amount": 0, "permission": "stocktake:approve", "level_name": "仓库复核"},
            {"doc_type": "requisition", "level": 1, "min_amount": 0, "permission": "requisition:approve", "level_name": "采购主管"},
            {"doc_type": "requisition", "level": 2, "min_amount": 5000, "permission": "finance:manage", "level_name": "财务复核"},
        ],
    },
    {
        "key": "strict",
        "name": "严控（大额三级）",
        "desc": "销售 5 万元加财务、20 万元再加总经理；采购 3 万元加财务。",
        "doc_types": ["sales", "purchase"],
        "rules": [
            {"doc_type": "sales", "level": 1, "min_amount": 0, "permission": "sales:approve", "level_name": "业务主管"},
            {"doc_type": "sales", "level": 2, "min_amount": 50000, "permission": "finance:manage", "level_name": "财务复核"},
            {"doc_type": "sales", "level": 3, "min_amount": 200000, "permission": "approval:manage", "level_name": "总经理审批"},
            {"doc_type": "purchase", "level": 1, "min_amount": 0, "permission": "purchase:approve", "level_name": "采购主管"},
            {"doc_type": "purchase", "level": 2, "min_amount": 30000, "permission": "finance:manage", "level_name": "财务复核"},
        ],
    },
]

TEMPLATE_MAP = {item["key"]: item for item in TEMPLATES}


def get_template(key: str) -> dict | None:
    return TEMPLATE_MAP.get(key)


def apply_template(db: Session, key: str, replace: bool = True) -> dict:
    """套用模板：默认先删掉这些单据类型下的旧规则，再写入模板规则。"""
    template = TEMPLATE_MAP.get(key)
    if not template:
        raise KeyError(key)
    removed = 0
    if replace:
        removed = (
            db.execute(
                delete(ApprovalRule).where(ApprovalRule.doc_type.in_(template["doc_types"]))
            ).rowcount
            or 0
        )
    existing_names = set(
        db.scalars(
            select(ApprovalRule.name).where(ApprovalRule.doc_type.in_(template["doc_types"]))
        ).all()
    )
    created = 0
    for index, rule in enumerate(template["rules"], start=1):
        name = f"{template['name']} · {rule['level_name']}"
        if name in existing_names:
            name = f"{name} #{index}"
        db.add(
            ApprovalRule(
                doc_type=rule["doc_type"],
                name=name,
                enabled=True,
                min_amount=float(rule.get("min_amount") or 0),
                max_amount=rule.get("max_amount"),
                level=int(rule["level"]),
                permission=rule.get("permission") or "",
                level_name=rule.get("level_name") or "",
                mode=rule.get("mode") or "single",
                approvers=rule.get("approvers") or "",
                conditions="",
            )
        )
        created += 1
    db.commit()
    return {
        "template": template["key"],
        "name": template["name"],
        "created": created,
        "removed": removed,
        "doc_types": list(template["doc_types"]),
    }
