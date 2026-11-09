from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..ai.tools import available_tools, execute
from ..ai.tools.base import ToolContext
from ..config import settings
from ..database import get_db
from ..deps import get_current_user, require_perm
from ..models import Product, PurchaseOrder, PurchaseOrderItem, User
from ..permissions import insight_types_for
from ..schemas import ChatRequest, ChatResponse
from ..scoping import scope_owner
from ..services import ai_service
from ..utils import gen_order_no

router = APIRouter(prefix="/api/ai", tags=["AI 智能"])


@router.get("/status", summary="AI 能力状态")
def status(user: User = Depends(get_current_user)):
    return {
        "llm_enabled": settings.llm_enabled,
        "mode": "大模型 + 数据洞察" if settings.llm_enabled else "内置规则引擎（可在 .env 配置大模型）",
        "role": user.role,
        "insight_types": sorted(insight_types_for(user.role)),
    }


@router.get("/insights", summary="经营洞察（按角色过滤）")
def insights(db: Session = Depends(get_db), user: User = Depends(require_perm("ai:insight"))):
    return ai_service.get_insights(db, insight_types_for(user.role), scope_owner(user))


@router.get("/replenishment", summary="智能补货建议")
def replenishment(
    lead_time: int = Query(15, ge=1, le=90),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("ai:replenish")),
):
    return ai_service.get_replenishment(db, lead_time)


@router.get("/forecast", summary="销售预测")
def forecast(
    product_id: int | None = Query(None),
    days: int = Query(14, ge=1, le=90),
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("ai:forecast")),
):
    try:
        return ai_service.get_forecast(db, product_id, days, scope_owner(user))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/chat", response_model=ChatResponse, summary="AI 经营助手问答")
def chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("ai:assist")),
):
    result = ai_service.chat(db, payload.message, [m.model_dump() for m in payload.history], user)
    return ChatResponse(**result)


@router.post("/replenishment/create-purchase", summary="一键生成补货采购单")
def create_purchase_from_suggestions(
    payload: dict | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("ai:replenish")),
    _: User = Depends(require_perm("purchase:manage")),
):
    product_ids = (payload or {}).get("product_ids")
    suggestions = ai_service.get_replenishment(db)
    if product_ids:
        suggestions = [s for s in suggestions if s["product_id"] in product_ids]
    suggestions = [s for s in suggestions if s["supplier_id"]]
    if not suggestions:
        raise HTTPException(status_code=400, detail="没有可生成的补货建议（需商品已关联供应商）")

    grouped: dict[int, list[dict]] = {}
    for s in suggestions:
        grouped.setdefault(s["supplier_id"], []).append(s)

    created = []
    for supplier_id, items in grouped.items():
        order = PurchaseOrder(
            order_no=gen_order_no(db, PurchaseOrder, "PO"),
            supplier_id=supplier_id,
            order_date=datetime.now(),
            status="draft",
            remark="由 AI 智能补货建议自动生成",
            created_by=user.username,
        )
        total = 0.0
        for item in items:
            product = db.get(Product, item["product_id"])
            unit_price = product.cost_price
            amount = round(item["suggested_qty"] * unit_price, 2)
            total += amount
            order.items.append(
                PurchaseOrderItem(
                    product_id=item["product_id"],
                    quantity=item["suggested_qty"],
                    unit_price=unit_price,
                    amount=amount,
                )
            )
        order.total_amount = round(total, 2)
        db.add(order)
        created.append({"order_no": order.order_no, "supplier_id": supplier_id, "items": len(items),
                        "total_amount": order.total_amount})
        db.flush()

    db.commit()
    return {"created": created, "count": len(created)}


# ---------------- 工具层 ----------------
class ToolRunRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    arguments: dict = Field(default_factory=dict)


@router.get("/tools", summary="AI 可调用能力清单（按角色过滤）")
def list_tools(user: User = Depends(get_current_user)):
    """只返回当前角色有权使用的工具，前端据此渲染可问的问题。"""
    return {"items": [tool.schema() for tool in available_tools(user.role)]}


@router.post("/tools/run", summary="执行一个 AI 只读能力")
def run_tool(
    payload: ToolRunRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """统一入口：鉴权、参数校验、数据范围与审计都在工具层完成。

    结果按 function-call 语义返回 200 + ok 字段；越权已在 execute() 内写入
    「AI 工具越权」审计日志，这里不再抛 403，避免审计中间件重复记录。
    """
    ctx = ToolContext(db=db, user=user, owner=scope_owner(user))
    result = execute(payload.name, payload.arguments, ctx)
    return {
        "ok": result.ok,
        "data": result.data,
        "error": result.error,
        "detail": result.error_detail,
    }
