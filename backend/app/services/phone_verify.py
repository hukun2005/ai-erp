"""注册/开户用的手机验证码。

验证码按「手机号 + 场景」维度一次性有效：发送新码即作废旧码，
校验成功后写 used，避免一码多用；尝试次数上限防止暴力枚举。

发送通道由 services.sms 负责：默认 console（写日志、不真实下发），
配置了 REGISTER_SMS_PROVIDER=aliyun 且填好 AccessKey 后自动切换为真实短信。
"""
import re
import secrets
import time
from datetime import datetime, timedelta
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import PhoneVerificationCode, User
from . import sms

SCENE_REGISTER = "register"  # 自助注册
SCENE_STAFF = "staff"  # 管理员为员工开户

PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")
COOLDOWN_SECONDS = 60

# 进程内冷却时间桶：桌面版单进程运行，够用且不引入额外依赖
_last_send: dict[str, float] = {}


class PhoneError(ValueError):
    """手机号或登录账号格式不合法，调用方转成 400。"""


def normalize_phone(phone: str) -> str:
    """去掉空格、横线与 +86 前缀，校验大陆 11 位手机号。"""
    value = re.sub(r"[ \-]", "", phone or "").strip()
    if value.startswith("+86"):
        value = value[3:]
    if not PHONE_PATTERN.match(value):
        raise PhoneError("请输入正确的 11 位手机号")
    return value


def normalize_username(username: str, phone: str) -> str:
    """未填登录账号时直接用手机号；填了则只允许字母、数字、下划线、点、横线。"""
    value = (username or "").strip() or phone
    if not USERNAME_PATTERN.match(value):
        raise PhoneError("登录账号只能包含字母、数字、下划线、点或横线，长度 3~50 位")
    return value


def mask_phone(phone: str) -> str:
    return f"{phone[:3]}****{phone[-4:]}" if len(phone) >= 7 else phone


def provider() -> str:
    """注册场景的发送通道；未单独配置时跟随 SMS_PROVIDER，都为空则 console 演练。"""
    return (settings.REGISTER_SMS_PROVIDER or settings.SMS_PROVIDER or "console").lower()


def phone_in_use(db: Session, phone: str) -> bool:
    return db.scalar(select(User).where(User.phone == phone)) is not None


def issue(db: Session, phone: str, scene: str) -> tuple[bool, str, str]:
    """下发验证码，返回 (是否成功, 提示, 演练模式下的明文验证码)。"""
    key = f"{scene}:{phone}"
    now = time.time()
    if now - _last_send.get(key, 0) < COOLDOWN_SECONDS:
        return False, f"验证码已发送，请 {COOLDOWN_SECONDS} 秒后再试", ""

    # 同一手机号同一场景只保留一个有效码，避免旧码继续可用
    for stale in db.scalars(
        select(PhoneVerificationCode)
        .where(PhoneVerificationCode.phone == phone)
        .where(PhoneVerificationCode.scene == scene)
        .where(PhoneVerificationCode.used == False)  # noqa: E712
    ).all():
        stale.used = True

    code = f"{secrets.randbelow(1_000_000):06d}"
    record = PhoneVerificationCode(
        phone=phone,
        scene=scene,
        code_hash=_hash(code),
        expires_at=datetime.now() + timedelta(minutes=settings.SMS_CODE_TTL_MINUTES),
    )
    db.add(record)
    db.flush()

    ok, message = sms.send_code(phone, code, provider=provider())
    if not ok:
        # 发送失败就作废本次码，避免留下一枚「从没送达」的有效码
        record.used = True
        db.flush()
        return False, f"短信发送失败：{message}", ""

    _last_send[key] = now
    dev_code = code if provider() == "console" else ""
    return True, f"验证码已发送，{settings.SMS_CODE_TTL_MINUTES} 分钟内有效", dev_code


def verify(db: Session, phone: str, scene: str, code: str, consume: bool = True) -> tuple[bool, str]:
    """校验验证码；consume=True 时消费掉，防止一码多用。"""
    record = db.scalar(
        select(PhoneVerificationCode)
        .where(PhoneVerificationCode.phone == phone)
        .where(PhoneVerificationCode.scene == scene)
        .where(PhoneVerificationCode.used == False)  # noqa: E712
        .order_by(PhoneVerificationCode.id.desc())
    )
    if not record:
        return False, "请先获取验证码"
    if record.expires_at < datetime.now():
        record.used = True
        db.flush()
        return False, "验证码已过期，请重新获取"
    if record.attempts >= settings.SMS_MAX_ATTEMPTS:
        record.used = True
        db.flush()
        return False, "尝试次数过多，请重新获取验证码"
    if not code or _hash(code) != record.code_hash:
        record.attempts += 1
        db.flush()
        remaining = max(settings.SMS_MAX_ATTEMPTS - record.attempts, 0)
        return False, f"验证码不正确，还可尝试 {remaining} 次"
    if consume:
        record.used = True
        db.flush()
    return True, ""


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
