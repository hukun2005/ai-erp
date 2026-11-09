"""工具注册表与执行器：所有 AI 能力调用的唯一入口。

安全顺序固定为「工具存在 → 角色有权限 → 参数合法 → 执行」：
无权限的请求在参数校验之前就被拒绝，避免该接口被当成数据结构探测工具。
"""
import logging

from pydantic import ValidationError

from ...audit import record
from ...permissions import has_permission
from .base import Tool, ToolContext, ToolResult

logger = logging.getLogger(__name__)

TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    """注册一个工具；同名重复注册直接报错，避免静默覆盖。"""
    if tool.name in TOOLS:
        raise ValueError(f"工具重复注册：{tool.name}")
    TOOLS[tool.name] = tool
    return tool


def available_tools(role: str) -> list[Tool]:
    """返回该角色可用的工具，供前端提示与模型 tool 列表使用。"""
    return [tool for tool in TOOLS.values() if has_permission(role, tool.required_perm)]


def _first_error(exc: ValidationError) -> str:
    """把 pydantic 校验错误压成一句可读提示。"""
    error = exc.errors()[0]
    field = ".".join(str(part) for part in error.get("loc", ()))
    message = error.get("msg", "")
    return (f"参数 {field} 不合法：{message}" if field else f"参数不合法：{message}")[:200]


def _row_count(data) -> int:
    """粗略统计返回条数，仅用于审计留痕。"""
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("rows", "items", "orders"):
            value = data.get(key)
            if isinstance(value, list):
                return len(value)
        return 1
    return 1


def execute(name: str, raw_args: dict | None, ctx: ToolContext) -> ToolResult:
    """执行一个工具并写审计日志。

    工具内部抛出的异常不会向上传播：对话链路不应因为一次查询失败而中断，
    同时错误文案保持通用，避免把内部结构暴露给模型或前端。
    """
    tool = TOOLS.get(name)
    if tool is None:
        return ToolResult.failure("unknown_tool", f"没有名为「{name}」的能力")

    if not has_permission(ctx.user.role, tool.required_perm):
        record(
            username=ctx.user.username,
            role=ctx.user.role,
            action="AI 工具越权",
            resource=f"工具 {name}"[:100],
            detail=f"缺少权限 {tool.required_perm}",
            result="denied",
            status_code=403,
        )
        return ToolResult.failure("forbidden", "当前角色无权查询该数据，请联系管理员调整权限")

    try:
        args = tool.params.model_validate(raw_args or {})
    except ValidationError as exc:
        return ToolResult.failure("invalid_args", _first_error(exc))

    try:
        data = tool.handler(ctx, args)
    except Exception:  # noqa: BLE001 工具失败只影响本次调用
        logger.exception("AI 工具执行失败：%s", name)
        return ToolResult.failure("tool_error", "查询执行失败，请稍后重试或联系管理员")

    record(
        username=ctx.user.username,
        role=ctx.user.role,
        action="AI 工具调用",
        resource=f"工具 {name}"[:100],
        detail=f"参数 {raw_args or {}}，返回 {_row_count(data)} 条",
        result="success",
    )
    return ToolResult.success(data)
