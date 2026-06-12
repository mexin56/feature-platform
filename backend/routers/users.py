from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_db, require_admin
from ..models import User
from ..services.auth import hash_password
from .auth import user_out

router = APIRouter(prefix="/users", tags=["users"])

ROLES = ("admin", "developer", "viewer")


class UserCreateIn(BaseModel):
    username: str
    password: str
    role: str = "developer"


class UserPatchIn(BaseModel):
    role: str | None = None
    is_active: bool | None = None


class ResetPasswordIn(BaseModel):
    new_password: str


@router.get("")
def list_users(db=Depends(get_db), _=Depends(require_admin)):
    return [user_out(u) for u in db.scalars(select(User).order_by(User.id)).all()]


@router.post("")
def create_user(body: UserCreateIn, db=Depends(get_db), _=Depends(require_admin)):
    if body.role not in ROLES:
        raise HTTPException(400, f"角色须为 {ROLES}")
    if len(body.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    if db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(400, "用户名已存在")
    u = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(u)
    db.commit()
    return user_out(u)


@router.patch("/{user_id}")
def patch_user(user_id: int, body: UserPatchIn, db=Depends(get_db), admin=Depends(require_admin)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "用户不存在")
    if u.id == admin.id and body.is_active is False:
        raise HTTPException(400, "不能禁用自己")
    if u.id == admin.id and body.role is not None and body.role != "admin":
        raise HTTPException(400, "不能降级自己的管理员角色")
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(400, f"角色须为 {ROLES}")
        u.role = body.role
    if body.is_active is not None:
        u.is_active = body.is_active
    db.commit()
    return user_out(u)


@router.post("/{user_id}/reset-password")
def reset_password(user_id: int, body: ResetPasswordIn, db=Depends(get_db), _=Depends(require_admin)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "用户不存在")
    if len(body.new_password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    u.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}
