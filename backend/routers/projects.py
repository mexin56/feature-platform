from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_current_user, get_db
from ..models import AuditLog, Project, ProjectMember, User
from ..services.audit import record

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectIn(BaseModel):
    name: str
    description: str = ""


class MemberIn(BaseModel):
    user_id: int


def _project_out(p: Project) -> dict:
    return {"id": p.id, "name": p.name, "description": p.description,
            "owner_id": p.owner_id, "created_at": p.created_at.isoformat()}


def _require_owner_or_admin(db, pid: int, user) -> Project:
    p = db.get(Project, pid)
    if p is None:
        raise HTTPException(404, "项目不存在")
    if user.role != "admin" and p.owner_id != user.id:
        raise HTTPException(403, "仅项目负责人或管理员可操作")
    return p


def _require_member_or_admin(db, pid: int, user) -> Project:
    p = db.get(Project, pid)
    if p is None:
        raise HTTPException(404, "项目不存在")
    if user.role != "admin":
        ok = db.scalar(select(ProjectMember).where(
            ProjectMember.project_id == pid, ProjectMember.user_id == user.id))
        if not ok:
            raise HTTPException(403, "不是该项目成员")
    return p


@router.get("")
def list_projects(db=Depends(get_db), user=Depends(get_current_user)):
    q = select(Project).order_by(Project.id)
    if user.role != "admin":
        q = q.join(ProjectMember, ProjectMember.project_id == Project.id).where(
            ProjectMember.user_id == user.id)
    return [_project_out(p) for p in db.scalars(q).all()]


@router.post("")
def create_project(body: ProjectIn, db=Depends(get_db), user=Depends(get_current_user)):
    if user.role == "viewer":
        raise HTTPException(403, "只读角色不能创建项目")
    if db.scalar(select(Project).where(Project.name == body.name)):
        raise HTTPException(400, "项目名已存在")
    p = Project(name=body.name, description=body.description, owner_id=user.id)
    db.add(p)
    db.flush()
    db.add(ProjectMember(project_id=p.id, user_id=user.id))
    record(db, user, "create_project", body.name, project_id=p.id)
    db.commit()
    return _project_out(p)


@router.post("/{pid}/members")
def add_member(pid: int, body: MemberIn, db=Depends(get_db), user=Depends(get_current_user)):
    _require_owner_or_admin(db, pid, user)
    if db.get(User, body.user_id) is None:
        raise HTTPException(404, "用户不存在")
    exists = db.scalar(select(ProjectMember).where(
        ProjectMember.project_id == pid, ProjectMember.user_id == body.user_id))
    if not exists:
        db.add(ProjectMember(project_id=pid, user_id=body.user_id))
        record(db, user, "add_member", f"user_id={body.user_id}", project_id=pid)
        db.commit()
    return {"ok": True}


@router.delete("/{pid}/members/{user_id}")
def remove_member(pid: int, user_id: int, db=Depends(get_db), user=Depends(get_current_user)):
    p = _require_owner_or_admin(db, pid, user)
    if user_id == p.owner_id:
        raise HTTPException(400, "不能移除项目负责人")
    m = db.scalar(select(ProjectMember).where(
        ProjectMember.project_id == pid, ProjectMember.user_id == user_id))
    if m:
        db.delete(m)
        record(db, user, "remove_member", f"user_id={user_id}", project_id=pid)
        db.commit()
    return {"ok": True}


@router.get("/{pid}/audit")
def list_audit(pid: int, db=Depends(get_db), user=Depends(get_current_user)):
    _require_member_or_admin(db, pid, user)
    rows = db.execute(
        select(AuditLog, User.username).join(User, AuditLog.user_id == User.id, isouter=True)
        .where(AuditLog.project_id == pid).order_by(AuditLog.id.desc()).limit(200)
    ).all()
    return [{"id": a.id, "action": a.action, "detail": a.detail,
             "username": name, "created_at": a.created_at.isoformat()}
            for a, name in rows]
