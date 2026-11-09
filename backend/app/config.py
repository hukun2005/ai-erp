import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# 源码运行时数据放项目目录；被打包成桌面应用后安装目录只读，
# 数据统一由 Electron 通过 AI_ERP_DATA_DIR 指到 %APPDATA%\AI-ERP\data。
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    RESOURCE_DIR = BASE_DIR

DATA_DIR = Path(os.getenv("AI_ERP_DATA_DIR") or BASE_DIR).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv(DATA_DIR / ".env")
if DATA_DIR != BASE_DIR:
    load_dotenv(BASE_DIR / ".env")

# 信用额度管控策略：off=不检查，warn=只在响应里提示，block=超额直接拦截确认出库
CREDIT_POLICIES = ("off", "warn", "block")


class Settings:
    """全局配置，优先读取 .env 环境变量。"""

    APP_NAME: str = "AI-ERP 智能管理系统"
    VERSION: str = "0.4.0"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-key-please-change-in-production")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "720"))
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'erp.db'}")

    # 大模型配置：留空时系统自动降级为内置规则引擎
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")

    # 问答链路调优参数：轮次、超时、采样与工具结果预算都放 .env，部署时改配置不改代码
    LLM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "45"))
    LLM_MAX_TOOL_ROUNDS: int = int(os.getenv("LLM_MAX_TOOL_ROUNDS", "6"))
    LLM_MAX_TOOL_CALLS: int = int(os.getenv("LLM_MAX_TOOL_CALLS", "12"))
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.2"))
    LLM_MAX_HISTORY: int = int(os.getenv("LLM_MAX_HISTORY", "8"))
    LLM_TOOL_RESULT_CHARS: int = int(os.getenv("LLM_TOOL_RESULT_CHARS", "6000"))

    # 视觉模型（入库单照片识别）：默认复用大模型端点与 Key
    # 智谱端点用 GLM-4V-Flash；其他厂商复用 LLM_MODEL，前提是该模型支持图片输入
    VISION_API_KEY: str = os.getenv("VISION_API_KEY", "") or LLM_API_KEY
    VISION_BASE_URL: str = os.getenv("VISION_BASE_URL", "") or LLM_BASE_URL
    VISION_MODEL: str = os.getenv("VISION_MODEL", "") or (
        "glm-4v-flash" if "bigmodel.cn" in VISION_BASE_URL else LLM_MODEL
    )
    VISION_TIMEOUT_SECONDS: float = float(os.getenv("VISION_TIMEOUT_SECONDS", "60"))
    # 识别前先在本地压缩：手机原图直接上传又慢又容易超限，最长边 2000 已足够看清单据小字
    VISION_MAX_EDGE: int = int(os.getenv("VISION_MAX_EDGE", "2000"))
    VISION_JPEG_QUALITY: int = int(os.getenv("VISION_JPEG_QUALITY", "88"))
    # 一次识别最多合并几页（多页送货单）
    VISION_MAX_PAGES: int = int(os.getenv("VISION_MAX_PAGES", "5"))

    UPLOAD_DIR: Path = Path(os.getenv("UPLOAD_DIR", str(DATA_DIR / "uploads")))
    MAX_UPLOAD_MB: int = int(os.getenv("MAX_UPLOAD_MB", "10"))

    # 桌面版由后端直接托管前端静态文件：Electron 只需访问一个地址，无需额外 Web 服务
    FRONTEND_DIR: Path = Path(os.getenv("FRONTEND_DIR", str(RESOURCE_DIR / "web")))

    CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # 飞书自定义机器人：填入 Webhook 地址即可向管理层群推送消息
    FEISHU_WEBHOOK_URL: str = os.getenv("FEISHU_WEBHOOK_URL", "")
    FEISHU_SECRET: str = os.getenv("FEISHU_SECRET", "")
    FEISHU_TIMEOUT_SECONDS: float = float(os.getenv("FEISHU_TIMEOUT_SECONDS", "8"))

    # 短信验证码（可选）：未配置时对账单自动降级为「口令」方式
    # SMS_PROVIDER 支持 console（写日志，供测试）与 aliyun（阿里云短信）
    SMS_PROVIDER: str = os.getenv("SMS_PROVIDER", "")
    SMS_SIGN_NAME: str = os.getenv("SMS_SIGN_NAME", "")
    SMS_TEMPLATE_CODE: str = os.getenv("SMS_TEMPLATE_CODE", "")
    ALIYUN_ACCESS_KEY_ID: str = os.getenv("ALIYUN_ACCESS_KEY_ID", "")
    ALIYUN_ACCESS_KEY_SECRET: str = os.getenv("ALIYUN_ACCESS_KEY_SECRET", "")
    SMS_CODE_TTL_MINUTES: int = int(os.getenv("SMS_CODE_TTL_MINUTES", "10"))
    SMS_MAX_ATTEMPTS: int = int(os.getenv("SMS_MAX_ATTEMPTS", "5"))

    # 注册/开户专用短信通道：留空则跟随 SMS_PROVIDER；两者都为空时默认 console 演练，
    # 不影响对账单的「口令模式」（对账单仍只在配置 SMS_PROVIDER 时启用短信）
    REGISTER_SMS_PROVIDER: str = os.getenv("REGISTER_SMS_PROVIDER", "")

    # 信用额度管控：off / warn / block，默认只提示不拦截，避免影响既有流程
    CREDIT_POLICY: str = os.getenv("CREDIT_POLICY", "warn").strip().lower()

    # 销售发货模式：
    #   outbound=审批通过只确认订单，库存由「出库单确认发货」扣减（默认，支持分批发货）；
    #   direct  =沿用旧行为，审批通过即扣库存，不要求出库单。
    SALES_SHIPPING_MODE: str = os.getenv("SALES_SHIPPING_MODE", "outbound").strip().lower()

    # 盘点差异是否必须二次审批：1=有差异先挂起，等第二个人复核后调整库存
    STOCKTAKE_APPROVAL: str = os.getenv("STOCKTAKE_APPROVAL", "1").strip()

    # 进程内定时调度器：没有它，库存预警只在业务动作发生时触发，没人动库存就永远不预警
    SCHEDULER_ENABLED: bool = os.getenv("SCHEDULER_ENABLED", "1") not in ("0", "false", "False", "")
    SCHEDULER_TICK_SECONDS: int = int(os.getenv("SCHEDULER_TICK_SECONDS", "60"))
    ALERT_SCAN_MINUTES: int = int(os.getenv("ALERT_SCAN_MINUTES", "30"))
    AUTOMATION_INTERVAL_MINUTES: int = int(os.getenv("AUTOMATION_INTERVAL_MINUTES", "10"))
    # 审批超时巡检周期（分钟）：扫描待审批单据，按超时规则提醒或升级
    APPROVAL_SLA_SCAN_MINUTES: int = int(os.getenv("APPROVAL_SLA_SCAN_MINUTES", "15"))

    # 数据备份：默认关闭；开启后由调度器按间隔（小时）自动生成完整备份
    BACKUP_AUTO_ENABLED: bool = os.getenv("BACKUP_AUTO_ENABLED", "0") not in ("0", "false", "False", "")
    BACKUP_AUTO_INTERVAL_HOURS: int = int(os.getenv("BACKUP_AUTO_INTERVAL_HOURS", "24"))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.LLM_API_KEY)

    @property
    def vision_enabled(self) -> bool:
        return bool(self.VISION_API_KEY)

    @property
    def feishu_enabled(self) -> bool:
        return bool(self.FEISHU_WEBHOOK_URL)

    @property
    def sms_enabled(self) -> bool:
        """配置了提供方才算启用；否则对账单走口令模式。"""
        return bool(self.SMS_PROVIDER)


settings = Settings()

# 安全加固：SECRET_KEY 仍为内置默认值时会明确告警，提醒管理员在生产环境切换到随机密钥
if settings.SECRET_KEY == "dev-secret-key-please-change-in-production":
    print(
        "\n[安全提醒] SECRET_KEY 仍为内置默认值，生产环境请务必修改（在 .env 中设置随机 SECRET_KEY）。\n"
        "可通过 `python -c \"import secrets; print(secrets.token_urlsafe(48))\"` 生成。"
    )

# 配置写错要在启动时立刻失败，不能悄悄退回某个默认策略
if settings.CREDIT_POLICY not in CREDIT_POLICIES:
    raise ValueError(
        f"CREDIT_POLICY 只能是 {'/'.join(CREDIT_POLICIES)}，当前为「{settings.CREDIT_POLICY}」"
    )

if settings.SALES_SHIPPING_MODE not in ("outbound", "direct"):
    raise ValueError(
        f"SALES_SHIPPING_MODE 只能是 outbound 或 direct，当前为「{settings.SALES_SHIPPING_MODE}」"
    )

_LLM_LIMITS = {
    "LLM_TIMEOUT_SECONDS": (settings.LLM_TIMEOUT_SECONDS, 1.0, 300.0),
    "LLM_MAX_TOOL_ROUNDS": (settings.LLM_MAX_TOOL_ROUNDS, 1, 12),
    "LLM_MAX_TOOL_CALLS": (settings.LLM_MAX_TOOL_CALLS, 1, 60),
    "LLM_TEMPERATURE": (settings.LLM_TEMPERATURE, 0.0, 2.0),
    "LLM_MAX_HISTORY": (settings.LLM_MAX_HISTORY, 0, 40),
    "LLM_TOOL_RESULT_CHARS": (settings.LLM_TOOL_RESULT_CHARS, 500, 60000),
}
for _name, (_value, _low, _high) in _LLM_LIMITS.items():
    if not _low <= _value <= _high:
        raise ValueError(f"{_name} 必须在 {_low}~{_high} 之间，当前为 {_value}")
del _name, _value, _low, _high
