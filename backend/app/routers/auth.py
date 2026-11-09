from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..permissions import role_meta
from ..schemas import LoginRequest, RegisterCodeRequest, RegisterRequest, Token, UserOut
from ..security import create_access_token, hash_password, verify_password
from ..services import phone_verify

router = APIRouter(prefix="/api/auth", tags=["认证"])


def user_out(user: User) -> UserOut:
    meta = role_meta(user.role)
    return UserOut(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        role_name=meta["name"],
        permissions=meta["permissions"],
        data_scope=meta["data_scope"],
        data_scope_name=meta["data_scope_name"],
        ai_prompts=meta["ai_prompts"],
    )


@router.post("/login", response_model=Token, summary="账号密码登录")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not verify_password(payload.password, user.hashed_password):
        record(
            username=payload.username,
            action="登录失败",
            resource="认证",
            detail="用户名或密码错误",
            result="failed",
            request=request,
            status_code=401,
        )
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        record(
            username=user.username,
            role=user.role,
            action="登录失败",
            resource="认证",
            detail="账号待审核或已被禁用",
            result="failed",
            request=request,
            status_code=403,
        )
        raise HTTPException(status_code=403, detail="账号待审核或已被禁用，请联系管理员")
    record(
        username=user.username,
        role=user.role,
        action="登录成功",
        resource="认证",
        detail=f"{role_meta(user.role)['name']} 登录系统",
        request=request,
    )
    return Token(
        access_token=create_access_token(user.id, user.username),
        user=user_out(user),
    )


@router.get("/me", response_model=UserOut, summary="获取当前登录用户及权限")
def me(user: User = Depends(get_current_user)):
    return user_out(user)


@router.post("/register/send-code", summary="注册-发送手机验证码")
def register_send_code(payload: RegisterCodeRequest, db: Session = Depends(get_db)):
    """公开接口：任何手机号都可以获取验证码，但注册后仍需管理员审核才能登录。"""
    try:
        phone = phone_verify.normalize_phone(payload.phone)
    except phone_verify.PhoneError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if phone_verify.phone_in_use(db, phone):
        raise HTTPException(status_code=400, detail="该手机号已绑定账号，请直接登录或联系管理员")

    ok, message, dev_code = phone_verify.issue(db, phone, phone_verify.SCENE_REGISTER)
    db.commit()
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {
        "ok": True,
        "message": message,
        "phone": phone_verify.mask_phone(phone),
        "dev_code": dev_code,
    }


@router.post("/register", summary="手机验证码自助注册（待审核）")
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    try:
        phone = phone_verify.normalize_phone(payload.phone)
        username = phone_verify.normalize_username(payload.username, phone)
    except phone_verify.PhoneError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if phone_verify.phone_in_use(db, phone):
        raise HTTPException(status_code=400, detail="该手机号已绑定账号，请直接登录或联系管理员")
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(status_code=400, detail="登录账号已被占用，请换一个")

    ok, error = phone_verify.verify(db, phone, phone_verify.SCENE_REGISTER, payload.code)
    if not ok:
        db.commit()
        raise HTTPException(status_code=400, detail=error)

    user = User(
        username=username,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name.strip(),
        role="sales",
        phone=phone,
        is_active=False,  # 自助注册一律待管理员审核
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    record(
        username=username,
        action="注册申请",
        resource=f"用户 {username}",
        detail=f"手机号 {phone_verify.mask_phone(phone)} 自助注册，待管理员审核",
        request=request,
    )
    return {
        "ok": True,
        "username": username,
        "message": "注册成功，请等待管理员审核通过后再登录",
    }
