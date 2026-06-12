import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_current_user, get_db, get_project_id
from ..models import Workflow, WorkflowVersion
from ..services.audit import record
from ..services.dag import DagError, validate_dag

router = APIRouter(prefix="/workflows", tags=["workflows"])

FAILURE_POLICIES = ("continue", "abort")


class WorkflowIn(BaseModel):
    name: str
    description: str = ""
    dag: dict
    cron: str | None = None
    timezone: str = "Asia/Shanghai"
    catchup: bool = False
    concurrency_limit: int = 1
    failure_policy: str = "continue"


def _validate_meta(body: WorkflowIn) -> None:
    try:
        validate_dag(body.dag)
    except DagError as e:
        raise HTTPException(400, str(e))
    if body.cron is not None:
        from croniter import croniter

        if not croniter.is_valid(body.cron):
            raise HTTPException(400, f"Cron 表达式非法: {body.cron}")
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(body.timezone)
    except Exception:
        raise HTTPException(400, f"时区非法: {body.timezone}")
    if body.failure_policy not in FAILURE_POLICIES:
        raise HTTPException(400, f"失败策略须为 {FAILURE_POLICIES}")
    if body.concurrency_limit < 1:
        raise HTTPException(400, "并发上限须 ≥1")


def _get_in_project(db, wid: int, pid: int) -> Workflow:
    wf = db.get(Workflow, wid)
    if wf is None or wf.project_id != pid:
        raise HTTPException(404, "工作流不存在")
    return wf


def _wf_out(db, wf: Workflow, with_dag: bool = False) -> dict:
    ver = db.get(WorkflowVersion, wf.current_version_id)
    out = {"id": wf.id, "name": wf.name, "description": wf.description, "cron": wf.cron,
           "timezone": wf.timezone, "catchup": wf.catchup,
           "concurrency_limit": wf.concurrency_limit, "failure_policy": wf.failure_policy,
           "status": wf.status, "version_no": ver.version_no if ver else None,
           "created_at": wf.created_at.isoformat()}
    if with_dag and ver:
        out["dag"] = json.loads(ver.dag_json)
    return out


@router.get("")
def list_workflows(db=Depends(get_db), pid=Depends(get_project_id)):
    rows = db.scalars(select(Workflow).where(Workflow.project_id == pid).order_by(Workflow.id)).all()
    return [_wf_out(db, w) for w in rows]


@router.post("")
def create_workflow(body: WorkflowIn, db=Depends(get_db),
                    user=Depends(get_current_user), pid=Depends(get_project_id)):
    _validate_meta(body)
    if db.scalar(select(Workflow).where(Workflow.project_id == pid, Workflow.name == body.name)):
        raise HTTPException(400, "同项目下工作流名已存在")
    wf = Workflow(project_id=pid, name=body.name, description=body.description, cron=body.cron,
                  timezone=body.timezone, catchup=body.catchup,
                  concurrency_limit=body.concurrency_limit, failure_policy=body.failure_policy,
                  created_by=user.id)
    db.add(wf)
    db.flush()
    ver = WorkflowVersion(workflow_id=wf.id, version_no=1,
                          dag_json=json.dumps(body.dag, ensure_ascii=False), created_by=user.id)
    db.add(ver)
    db.flush()
    wf.current_version_id = ver.id
    record(db, user, "create_workflow", body.name, project_id=pid)
    db.commit()
    return _wf_out(db, wf)


@router.get("/{wid}")
def get_workflow(wid: int, db=Depends(get_db), pid=Depends(get_project_id)):
    return _wf_out(db, _get_in_project(db, wid, pid), with_dag=True)


@router.put("/{wid}")
def update_workflow(wid: int, body: WorkflowIn, db=Depends(get_db),
                    user=Depends(get_current_user), pid=Depends(get_project_id)):
    wf = _get_in_project(db, wid, pid)
    _validate_meta(body)
    if wf.status == "online" and body.cron is None:
        raise HTTPException(400, "上线中的工作流不能清除 Cron 表达式,请先下线")
    dup = db.scalar(select(Workflow).where(
        Workflow.project_id == pid, Workflow.name == body.name, Workflow.id != wid))
    if dup:
        raise HTTPException(400, "同项目下工作流名已存在")
    wf.name, wf.description, wf.cron = body.name, body.description, body.cron
    wf.timezone, wf.catchup = body.timezone, body.catchup
    wf.concurrency_limit, wf.failure_policy = body.concurrency_limit, body.failure_policy
    cur = db.get(WorkflowVersion, wf.current_version_id)
    new_dag = json.dumps(body.dag, ensure_ascii=False)
    if cur is None or cur.dag_json != new_dag:
        next_no = (db.scalar(select(WorkflowVersion.version_no)
                             .where(WorkflowVersion.workflow_id == wid)
                             .order_by(WorkflowVersion.version_no.desc())) or 0) + 1
        ver = WorkflowVersion(workflow_id=wid, version_no=next_no, dag_json=new_dag,
                              created_by=user.id)
        db.add(ver)
        db.flush()
        wf.current_version_id = ver.id
    record(db, user, "update_workflow", wf.name, project_id=pid)
    db.commit()
    return _wf_out(db, wf)


@router.get("/{wid}/versions")
def list_versions(wid: int, db=Depends(get_db), pid=Depends(get_project_id)):
    _get_in_project(db, wid, pid)
    rows = db.scalars(select(WorkflowVersion).where(WorkflowVersion.workflow_id == wid)
                      .order_by(WorkflowVersion.version_no.desc())).all()
    return [{"version_no": v.version_no, "created_by": v.created_by,
             "created_at": v.created_at.isoformat()} for v in rows]


@router.post("/{wid}/online")
def online(wid: int, db=Depends(get_db), user=Depends(get_current_user), pid=Depends(get_project_id)):
    wf = _get_in_project(db, wid, pid)
    if not wf.cron:
        raise HTTPException(400, "未配置 Cron,无法上线定时调度")
    wf.status = "online"
    record(db, user, "online_workflow", wf.name, project_id=pid)
    db.commit()
    return {"ok": True}


@router.post("/{wid}/offline")
def offline(wid: int, db=Depends(get_db), user=Depends(get_current_user), pid=Depends(get_project_id)):
    wf = _get_in_project(db, wid, pid)
    wf.status = "offline"
    record(db, user, "offline_workflow", wf.name, project_id=pid)
    db.commit()
    return {"ok": True}
