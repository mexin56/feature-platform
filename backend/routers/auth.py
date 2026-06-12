from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_current_user, get_db, get_settings
from ..models import User
from ..services.auth import create_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


def user_out(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role, "is_active": u.is_active}


@router.post("/login")
def login(body: LoginIn, db=Depends(get_db), settings=Depends(get_settings)):
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    return {"token": create_token(user.id, settings.storage_dir), "user": user_out(user)}


@router.get("/me")
def me(user=Depends(get_current_user)):
    return user_out(user)


@router.post("/change-password")
def change_password(body: ChangePasswordIn, user=Depends(get_current_user), db=Depends(get_db)):
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "原密码错误")
    if len(body.new_password) < 6:
        raise HTTPException(400, "新密码至少 6 位")
    db.get(User, user.id).password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}
