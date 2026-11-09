"""补货测算工具：把「智能补货」页面背后的同一套算法开放给模型调用。

工具只给建议，不写库：真正的采购单仍由用户在前端确认后生成。
"""
from pydantic import BaseModel, Field

from ...services.replenishment import get_replenishment
from .base import Tool, ToolContext
from .registry import register


class ReplenishmentParams(BaseModel):
    lead_time: int = Field(15, ge=1, le=90, description="采购周期（天），默认 15")
    limit: int = Field(10, ge=1, le=50, description="最多返回多少个商品")


def _replenishment_plan(ctx: ToolContext, params: ReplenishmentParams) -> dict:
    suggestions = get_replenishment(ctx.db, params.lead_time)[: params.limit]
    return {
        "lead_time": params.lead_time,
        "count": len(suggestions),
        "rows": suggestions,
        "estimated_cost": round(sum(item["estimated_cost"] for item in suggestions), 2),
        "note": (
            "补货量 = 日均销量×(采购周期+10)+1.2×安全库存−现有库存−在途采购；"
            "这只是建议，需用户在前端「智能补货」里确认后才会生成采购单"
        ),
    }


register(
    Tool(
        name="get_replenishment_plan",
        title="智能补货测算",
        description=(
            "按近 30 天日均销量、安全库存、在途采购和采购周期，算出每个商品建议补多少、"
            "找哪个供应商、预计花多少钱。用于回答「哪些商品要补货」「帮我生成补货方案」"
            "「这次补货要花多少钱」。只给建议，不会建单。"
        ),
        params=ReplenishmentParams,
        required_perm="ai:replenish",
        handler=_replenishment_plan,
    )
)
