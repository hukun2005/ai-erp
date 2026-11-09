"""飞书（Lark）消息推送：对接群自定义机器人 Webhook。

只依赖标准库。Webhook 与签名密钥由管理员在「飞书通知」页面填写（存于数据库），
调用时通过参数传入，因此接入飞书不需要修改代码或重启服务；settings 中的
FEISHU_WEBHOOK_URL 仅作为未保存页面配置时的兜底。
"""
import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from ..config import settings

logger = logging.getLogger(__name__)


class FeishuError(Exception):
    """飞书推送失败，message 可直接返回给前端。"""


def validate_webhook(url: str) -> str:
    """校验并规范化 Webhook 地址，非法时抛 ValueError（路由层转为 400）。"""
    url = (url or "").strip()
    if not url:
        raise ValueError("请填写飞书机器人 Webhook 地址")
    if len(url) > 300:
        raise ValueError("Webhook 地址过长，最多 300 个字符")
    if not url.startswith(("http://", "https://")):
        raise ValueError("Webhook 地址必须以 http:// 或 https:// 开头")
    if "open-apis/bot" not in url:
        raise ValueError(
            "地址格式不正确，应形如 https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx"
        )
    return url


def masked(url: str) -> str:
    """界面展示用：保留协议、域名与尾部片段，隐藏完整凭据。"""
    if not url:
        return ""
    parts = urllib.parse.urlsplit(url)
    tail = parts.path.rsplit("/", 1)[-1]
    host = f"{parts.scheme}://{parts.netloc}" if parts.scheme else "https://open.feishu.cn"
    if len(tail) > 10:
        return f"{host}/.../{tail[:6]}***{tail[-4:]}"
    return f"{host}/***"


def _sign(timestamp: str, secret: str) -> str:
    """飞书签名：以 timestamp + '\\n' + secret 为消息，secret 为密钥做 HMAC-SHA256。"""
    key = secret.encode("utf-8")
    message = f"{timestamp}\n{secret}".encode("utf-8")
    return base64.b64encode(hmac.new(key, message, hashlib.sha256).digest()).decode("utf-8")


def build_card(title: str, template: str, lines: list[str], footer: str = "") -> dict:
    """组装飞书交互卡片：标题 + 富文本正文 + 页脚说明。"""
    elements: list[dict] = []
    if lines:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
    if footer:
        elements.append({"tag": "hr"})
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": footer}]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": template, "title": {"tag": "plain_text", "content": title}},
        "elements": elements,
    }


def send_card(card: dict, webhook_url: str, secret: str = "") -> dict:
    """推送卡片消息，成功返回 {ok, code, msg}，失败抛出 FeishuError。"""
    if not webhook_url:
        raise FeishuError("尚未配置飞书机器人，请先在「飞书通知」页面填写 Webhook 地址并保存")
    if not isinstance(card, dict) or not card.get("header"):
        raise FeishuError("消息内容不合法：缺少卡片标题")

    payload: dict = {"msg_type": "interactive", "card": card}
    if secret:
        timestamp = str(int(time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = _sign(timestamp, secret)

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.FEISHU_TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:200] if exc.fp else ""
        logger.warning("飞书 Webhook 返回 HTTP %s：%s", exc.code, detail)
        raise FeishuError(f"飞书接口返回 HTTP {exc.code}，请检查 Webhook 地址是否正确") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("连接飞书 Webhook 失败：%s", exc)
        raise FeishuError("连接飞书失败，请检查网络或 Webhook 地址") from exc

    try:
        data = json.loads(raw) if raw else {}
    except ValueError as exc:
        raise FeishuError("飞书返回内容无法解析，请确认地址是群机器人 Webhook") from exc

    code = data.get("code", data.get("StatusCode", 0))
    if code not in (0, None, "0"):
        message = data.get("msg") or data.get("StatusMessage") or f"返回错误码 {code}"
        raise FeishuError(f"飞书推送失败：{message}")
    return {"ok": True, "code": 0, "msg": "发送成功"}
