"""AI 工具层的基础类型。

工具是 ERP 能力的最小可调度单元：模型只负责「选哪个工具、填什么参数」，
鉴权、数据范围过滤和审计全部在服务端完成，模型无法绕过。
"""
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...models import User


@dataclass(frozen=True)
class ToolContext:
    """一次工具调用的服务端上下文。

    owner 由角色数据范围推导（scoping.scope_owner），不接受模型传入，
    否则模型只要多填一个参数就能读到别人的数据。
    """

    db: Session
    user: User
    owner: str | None

    @property
    def role(self) -> str:
        """当前调用者的角色 key。"""
        return self.user.role


@dataclass(frozen=True)
class ToolResult:
    """工具执行结果。

    error 供调用方做机器判断，error_detail 才是给用户看的文案；
    两者都不允许携带无权查看的数据本身。
    """

    ok: bool
    data: Any = None
    error: str = ""
    error_detail: str = ""

    @classmethod
    def success(cls, data: Any) -> "ToolResult":
        """构造成功结果。"""
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, error: str, detail: str = "") -> "ToolResult":
        """构造失败结果。"""
        return cls(ok=False, error=error, error_detail=detail)


@dataclass(frozen=True)
class Tool:
    """一个可被 AI 调度的能力。

    required_perm 必须取自 permissions.ALL_PERMISSIONS，与 REST 接口共用同一套权限 key，
    保证「菜单里能点到的」和「AI 能问到的」永远一致，不会各自漂移。
    """

    name: str
    title: str
    description: str
    params: type[BaseModel]
    required_perm: str
    handler: Callable[[ToolContext, Any], Any]
    read_only: bool = True

    def schema(self) -> dict:
        """返回给模型/前端的能力描述，参数 JSON Schema 由参数模型生成。"""
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "parameters": self.params.model_json_schema(),
            "read_only": self.read_only,
            "required_perm": self.required_perm,
        }
