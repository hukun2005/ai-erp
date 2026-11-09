from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import record
from ..database import get_db
from ..deps import get_current_user, require_perm
from ..models import User
from ..permissions import ROLE_DEFINITIONS, role_meta
from ..schemas import PasswordReset, RegisterCodeRequest, StaffCreate, UserCreate, UserDetail, UserUpdate
from ..security import hash_password
from ..services import phone_verify

router = APIRouter(prefix="/api/users", tags=["用户与角色"], dependencies=[Depends(require_perm("user:manage"))])


def _detail(user: User) -> UserDetail:
    return UserDetail(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        phone=user.phone or "",
        role=user.role,
        role_name=role_meta(user.role)["name"],
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.get("/roles", summary="角色及权限说明")
def list_roles():
    return [
        {
            "role": key,
            "name": value["name"],
            "color": value["color"],
            "description": value["description"],
            "permissions": role_meta(key)["permissions"],
            "data_scope": role_meta(key)["data_scope"],
            "data_scope_name": role_meta(key)["data_scope_name"],
            "ai_prompts": value["ai_prompts"],
        }
        for key, value in ROLE_DEFINITIONS.items()
    ]


@router.get("", summary="用户列表")
def list_users(
    keyword: str = Query(""),
    role: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = select(User)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where((User.username.like(like)) | (User.full_name.like(like)))
    if role:
        stmt = stmt.where(User.role == role)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(User.id).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"total": total, "items": [_detail(u) for u in rows]}


@router.post("/send-code", summary="手机号开户-发送验证码")
def staff_send_code(payload: RegisterCodeRequest, db: Session = Depends(get_db)):
    """管理员为员工开户：验证码发到员工手机，证明该手机号归其本人所有。"""
    try:
        phone = phone_verify.normalize_phone(payload.phone)
    except phone_verify.PhoneError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if phone_verify.phone_in_use(db, phone):
        raise HTTPException(status_code=400, detail="该手机号已绑定其他账号")

    ok, message, dev_code = phone_verify.issue(db, phone, phone_verify.SCENE_STAFF)
    db.commit()
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {
        "ok": True,
        "message": message,
        "phone": phone_verify.mask_phone(phone),
        "dev_code": dev_code,
    }


@router.post("/register", response_model=UserDetail, summary="管理员为员工注册账号")
def staff_register(
    payload: StaffCreate,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
):
    if payload.role not in ROLE_DEFINITIONS:
        raise HTTPException(status_code=400, detail="角色不存在")
    try:
        phone = phone_verify.normalize_phone(payload.phone)
        username = phone_verify.normalize_username(payload.username, phone)
    except phone_verify.PhoneError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if phone_verify.phone_in_use(db, phone):
        raise HTTPException(status_code=400, detail="该手机号已绑定其他账号")
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(status_code=400, detail="登录账号已存在")

    ok, error = phone_verify.verify(db, phone, phone_verify.SCENE_STAFF, payload.code)
    if not ok:
        db.commit()
        raise HTTPException(status_code=400, detail=error)

    user = User(
        username=username,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name.strip(),
        role=payload.role,
        phone=phone,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    record(
        username=current.username,
        role=current.role,
        action="手机号开户",
        resource=f"用户 {user.username}",
        detail=f"为 {phone_verify.mask_phone(phone)} 注册账号，角色：{role_meta(user.role)['name']}",
        request=request,
    )
    return _detail(user)


@router.post("", response_model=UserDetail, summary="新建用户")
def create_user(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
):
    if payload.role not in ROLE_DEFINITIONS:
        raise HTTPException(status_code=400, detail="角色不存在")
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status_code=400, detail="用户名已存在")
    user = User(
        username=payload.username,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    record(
        username=current.username,
        role=current.role,
        action="新建用户",
        resource=f"用户 {user.username}",
        detail=f"分配角色：{role_meta(user.role)['name']}",
        request=request,
    )
    return _detail(user)


@router.put("/{user_id}", response_model=UserDetail, summary="更新用户角色/状态")
def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    data = payload.model_dump(exclude_unset=True)
    old_role, old_active = user.role, user.is_active
    if "role" in data:
        if data["role"] not in ROLE_DEFINITIONS:
            raise HTTPException(status_code=400, detail="角色不存在")
        if user.id == current.id and data["role"] != "admin":
            raise HTTPException(status_code=400, detail="不能修改自己的管理员角色")
    if "is_active" in data and user.id == current.id and not data["is_active"]:
        raise HTTPException(status_code=400, detail="不能禁用当前登录账号")
    for key, value in data.items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    changes = []
    if user.role != old_role:
        changes.append(f"角色 {role_meta(old_role)['name']} → {role_meta(user.role)['name']}")
    if user.is_active != old_active:
        changes.append("状态 启用 → 禁用" if not user.is_active else "状态 禁用 → 启用")
    record(
        username=current.username,
        role=current.role,
        action="更新用户",
        resource=f"用户 {user.username}",
        detail="；".join(changes) or "更新用户资料",
        request=request,
    )
    return _detail(user)


@router.post("/{user_id}/reset-password", summary="重置密码")
def reset_password(
    user_id: int,
    payload: PasswordReset,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.hashed_password = hash_password(payload.password)
    db.commit()
    record(
        username=current.username,
        role=current.role,
        action="重置密码",
        resource=f"用户 {user.username}",
        detail="管理员重置了该账号的登录密码",
        request=request,
    )
    return {"ok": True}


@router.delete("/{user_id}", summary="删除用户")
def delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == current.id:
        raise HTTPException(status_code=400, detail="不能删除当前登录账号")
    username = user.username
    db.delete(user)
    db.commit()
    record(
        username=current.username,
        role=current.role,
        action="删除用户",
        resource=f"用户 {username}",
        detail="账号及登录权限已移除",
        request=request,
    )
    return {"ok": True}
