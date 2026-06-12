from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select


def get_db(request: Request):
    SessionLocal = request.app.state.sessionmaker
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_settings(request: Request):
    return request.app.state.settings


def get_current_user(request: Request, db=Depends(get_db)):
    """JWT 认证;viewer 只读控制(非 GET 拒绝)。"""
    from .models import User
    from .services.auth import decode_token

    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "未登录")
    uid = decode_token(header[7:], request.app.state.settings.storage_dir)
    if uid is None:
        raise HTTPException(401, "登录已过期,请重新登录")
    user = db.get(User, uid)
    if user is None or not user.is_active:
        raise HTTPException(401, "账号不存在或已禁用")
    if user.role == "viewer" and request.method not in ("GET", "HEAD", "OPTIONS"):
        raise HTTPException(403, "只读角色无权执行此操作")
    return user


def require_admin(user=Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


def get_project_id(request: Request, db=Depends(get_db), user=Depends(get_current_user)):
    """当前项目(X-Project-Id 头);校验成员资格。admin 全部可见。"""
    from .models import ProjectMember

    raw = request.headers.get("x-project-id", "").strip()
    if not raw:
        raise HTTPException(400, "缺少 X-Project-Id")
    try:
        pid = int(raw)
    except ValueError:
        raise HTTPException(400, "X-Project-Id 非法")
    if user.role != "admin":
        member = db.scalar(
            select(func.count(ProjectMember.id)).where(
                ProjectMember.project_id == pid, ProjectMember.user_id == user.id
            )
        )
        if not member:
            raise HTTPException(403, "不是该项目成员")
    return pid
