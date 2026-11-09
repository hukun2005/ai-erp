"""短信验证码发送。

两个提供方：
- console：把验证码写进日志，用于本地联调与自检，不产生任何外部调用；
- aliyun：阿里云短信服务（RPC 签名 v1.0），配置 AccessKey 后即可真实下发。

未配置 SMS_PROVIDER 时，对账单自动降级为「口令」模式：
业务员把 6 位口令单独发给客户，同样需要两个东西才能打开。
"""
import base64
import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

ALIYUN_ENDPOINT = "https://dysmsapi.aliyuncs.com/"


def _percent(value: str) -> str:
    """阿里云要求的 RFC3986 编码：空格用 %20，* 用 %2A，~ 不编码。"""
    return quote(str(value), safe="~")


def sign_aliyun(params: dict, secret: str) -> str:
    """按阿里云 RPC 签名规则计算 Signature（HMAC-SHA1 + Base64）。"""
    canonical = "&".join(f"{_percent(key)}={_percent(params[key])}" for key in sorted(params))
    to_sign = f"GET&{_percent('/')}&{_percent(canonical)}"
    digest = hmac.new(f"{secret}&".encode(), to_sign.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def _build_aliyun_params(phone: str, code: str) -> dict:
    params = {
        "AccessKeyId": settings.ALIYUN_ACCESS_KEY_ID,
        "Action": "SendSms",
        "Format": "JSON",
        "PhoneNumbers": phone,
        "RegionId": "cn-hangzhou",
        "SignName": settings.SMS_SIGN_NAME,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex,
        "SignatureVersion": "1.0",
        "TemplateCode": settings.SMS_TEMPLATE_CODE,
        "TemplateParam": f'{{"code":"{code}"}}',
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Version": "2017-05-25",
    }
    params["Signature"] = sign_aliyun(params, settings.ALIYUN_ACCESS_KEY_SECRET)
    return params


def send_code(phone: str, code: str, provider: str | None = None) -> tuple[bool, str]:
    """下发验证码，返回 (是否成功, 说明)。失败不抛异常，由调用方决定降级或报错。"""
    if not phone:
        return False, "缺少手机号，无法发送短信"
    provider = (provider or settings.SMS_PROVIDER or "").lower()

    if provider == "console":
        # 仅用于联调：不真的发短信，但把码打出来便于验证流程
        logger.info("【短信模拟】发送到 %s 的验证码：%s", phone, code)
        return True, "console"

    if provider == "aliyun":
        if not (settings.ALIYUN_ACCESS_KEY_ID and settings.ALIYUN_ACCESS_KEY_SECRET):
            return False, "阿里云短信未配置 AccessKey"
        if not (settings.SMS_SIGN_NAME and settings.SMS_TEMPLATE_CODE):
            return False, "阿里云短信未配置签名或模板"
        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(ALIYUN_ENDPOINT, params=_build_aliyun_params(phone, code))
                body = response.json()
            if body.get("Code") == "OK":
                return True, "aliyun"
            logger.warning("阿里云短信返回异常：%s", body)
            return False, str(body.get("Message") or "短信发送失败")
        except Exception:  # noqa: BLE001 短信失败不应阻断对账流程
            logger.exception("调用阿里云短信失败")
            return False, "短信服务暂时不可用"

    return False, f"未知的短信提供方：{provider}"
