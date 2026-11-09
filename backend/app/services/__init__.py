"""业务服务层。

这里只预导入无反向依赖的模块。ai_service 依赖 app.ai.tools，而工具层又要用 analytics，
若在包初始化时导入 ai_service 会形成循环，因此它改为按需导入：
调用方写 `from ..services import ai_service` 仍可正常工作（Python 会加载对应子模块）。
"""
from . import analytics  # noqa: F401
