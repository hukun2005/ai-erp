"""自动化规则管理接口。

规则本身只是配置，真正的执行在 services/automation.py；
这里提供增删改查、试运行与执行记录查询。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import require_perm
from ..models import AutomationRule, AutomationRun, User
from ..services import automation

router = APIRouter(prefix="/api/automations", tags=["自动化规则"])


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = False
    trigger_type: str = Field(min_length=1, max_length=30)
    trigger_config: dict = Field(default_factory=dict)
    action_type: str = Field(min_length=1, max_length=30)
    action_config: dict = Field(default_factory=dict)
    cooldown_minutes: int = Field(240, ge=0, le=10080)


class RuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    enabled: bool | None = None
    trigger_type: str | None = Field(default=None, min_length=1, max_length=30)
    trigger_config: dict | None = None
    action_type: str | None = Field(default=None, min_length=1, max_length=30)
    action_config: dict | None = None
    cooldown_minutes: int | None = Field(default=None, ge=0, le=10080)


class RunIn(BaseModel):
    dry_run: bool = False


def _serialize(rule: AutomationRule) -> dict:
    trigger = automation.TRIGGERS.get(rule.trigger_type, {})
    action = automation.ACTIONS.get(rule.action_type, {})
    return {
        "id": rule.id,
        "name": rule.name,
        "enabled": rule.enabled,
        "trigger_type": rule.trigger_type,
        "trigger_label": trigger.get("label", rule.trigger_type),
        "trigger_description": trigger.get("description", ""),
        "trigger_config": rule.trigger_config or {},
        "action_type": rule.action_type,
        "action_label": action.get("label", rule.action_type),
        "action_description": action.get("description", ""),
        "action_config": rule.action_config or {},
        "cooldown_minutes": rule.cooldown_minutes,
        "last_run_at": rule.last_run_at,
        "last_status": rule.last_status,
        "run_count": rule.run_count,
        "created_by": rule.created_by,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


def _serialize_run(run: AutomationRun) -> dict:
    return {
        "id": run.id,
        "rule_id": run.rule_id,
        "rule_name": run.rule_name,
        "status": run.status,
        "summary": run.summary,
        "detail": run.detail,
        "payload": run.payload or {},
        "created_at": run.created_at,
    }


def _validate(trigger_type: str | None, action_type: str | None) -> None:
    if trigger_type is not None and trigger_type not in automation.TRIGGERS:
        raise HTTPException(status_code=400, detail=f"不支持的触发条件：{trigger_type}")
    if action_type is not None and action_type not in automation.ACTIONS:
        raise HTTPException(status_code=400, detail=f"不支持的动作类型：{action_type}")


@router.get("/options", summary="可用的触发条件与动作")
def rule_options(user: User = Depends(require_perm("automation:view"))):
    return automation.options()


@router.get("/runs", summary="最近执行记录")
def recent_runs(
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("automation:view")),
):
    rows = db.scalars(select(AutomationRun).order_by(AutomationRun.id.desc()).limit(limit)).all()
    return [_serialize_run(row) for row in rows]


@router.get("", summary="规则列表")
def list_rules(
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("automation:view")),
):
    rows = db.scalars(select(AutomationRule).order_by(AutomationRule.id)).all()
    return [_serialize(rule) for rule in rows]


@router.post("", summary="新建规则")
def create_rule(
    payload: RuleIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("automation:manage")),
):
    _validate(payload.trigger_type, payload.action_type)
    rule = AutomationRule(
        name=payload.name.strip(),
        enabled=payload.enabled,
        trigger_type=payload.trigger_type,
        trigger_config=payload.trigger_config,
        action_type=payload.action_type,
        action_config=payload.action_config,
        cooldown_minutes=payload.cooldown_minutes,
        created_by=user.username,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    record(
        username=user.username,
        role=user.role,
        action="新建自动化规则",
        resource=f"自动化规则 {rule.name}",
        detail=f"{rule.trigger_type} → {rule.action_type}",
        request=request,
    )
    return _serialize(rule)


@router.put("/{rule_id}", summary="修改规则")
def update_rule(
    rule_id: int,
    payload: RuleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("automation:manage")),
):
    rule = db.get(AutomationRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    data = payload.model_dump(exclude_unset=True)
    _validate(data.get("trigger_type"), data.get("action_type"))
    for key, value in data.items():
        if key == "name" and value is not None:
            value = value.strip()
        setattr(rule, key, value)
    db.commit()
    db.refresh(rule)
    record(
        username=user.username,
        role=user.role,
        action="修改自动化规则",
        resource=f"自动化规则 {rule.name}",
        detail="、".join(data.keys()),
        request=request,
    )
    return _serialize(rule)


@router.delete("/{rule_id}", summary="删除规则")
def delete_rule(
    rule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("automation:manage")),
):
    rule = db.get(AutomationRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    name = rule.name
    db.execute(delete(AutomationRun).where(AutomationRun.rule_id == rule_id))
    db.delete(rule)
    db.commit()
    record(
        username=user.username,
        role=user.role,
        action="删除自动化规则",
        resource=f"自动化规则 {name}",
        request=request,
    )
    return {"ok": True}


@router.post("/{rule_id}/run", summary="执行或试运行规则")
def run_rule(
    rule_id: int,
    payload: RunIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("automation:manage")),
):
    rule = db.get(AutomationRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    result = automation.run_rule(db, rule, dry_run=payload.dry_run, operator=user.username)
    record(
        username=user.username,
        role=user.role,
        action="试运行自动化规则" if payload.dry_run else "执行自动化规则",
        resource=f"自动化规则 {rule.name}",
        detail=result["summary"],
        request=request,
    )
    return result


@router.get("/{rule_id}/runs", summary="某条规则的执行记录")
def rule_runs(
    rule_id: int,
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("automation:view")),
):
    rows = db.scalars(
        select(AutomationRun)
        .where(AutomationRun.rule_id == rule_id)
        .order_by(AutomationRun.id.desc())
        .limit(limit)
    ).all()
    return [_serialize_run(row) for row in rows]
