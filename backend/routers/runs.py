"""Runs API: 手工触发 / 按区间补数 / 列表 / 详情 / 运维操作。
项目隔离:所有端点通过 get_project_id 确认调用方与工作流同属一个项目。
审计:trigger_run / backfill / stop_run / retry_run / mark_success 写 audit_logs。"""
import json
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_current_user, get_db, get_project_id
from ..models import TaskInstance, Workflow, WorkflowRun, WorkflowVersion
from ..services.audit import record

router = APIRouter(tags=["runs"])


class TriggerIn(BaseModel):
    data_interval_start: datetime | None = None
    data_interval_end: datetime | None = None


class BackfillIn(BaseModel):
    start_date: datetime
    end_date: datetime
    parallel: int = 1


def _wf_in_project(db, wid: int, pid: int) -> Workflow:
    wf = db.get(Workflow, wid)
    if wf is None or wf.project_id != pid:
        raise HTTPException(404, "工作流不存在")
    return wf


def _run_in_project(db, rid: int, pid: int) -> WorkflowRun:
    run = db.get(WorkflowRun, rid)
    if run is None:
        raise HTTPException(404, "实例不存在")
    _wf_in_project(db, run.workflow_id, pid)
    return run


def _run_out(run: WorkflowRun) -> dict:
    return {"id": run.id, "workflow_id": run.workflow_id, "run_type": run.run_type,
            "state": run.state, "parallel_degree": run.parallel_degree,
            "data_interval_start": run.data_interval_start.isoformat(),
            "data_interval_end": run.data_interval_end.isoformat(),
            "created_at": run.created_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None}


def _latest_interval(cron: str, now_local: datetime) -> tuple[datetime, datetime]:
    from croniter import croniter

    it = croniter(cron, now_local)
    end = it.get_prev(datetime)
    start = it.get_prev(datetime)
    return start, end


@router.post("/workflows/{wid}/trigger")
def trigger_run(wid: int, body: TriggerIn, db=Depends(get_db),
                user=Depends(get_current_user), pid=Depends(get_project_id)):
    from ..services.scheduler import Scheduler

    wf = _wf_in_project(db, wid, pid)
    ver = db.get(WorkflowVersion, wf.current_version_id)
    if ver is None:
        raise HTTPException(400, "工作流缺少版本")
    sched = Scheduler(None)  # 仅用其纯函数能力(create_run/_now_local),不触碰 SessionLocal
    if body.data_interval_start and body.data_interval_end:
        s, e = body.data_interval_start, body.data_interval_end
        if e < s:
            raise HTTPException(400, "区间终点早于起点")
    elif wf.cron:
        s, e = _latest_interval(wf.cron, sched._now_local(wf.timezone))
    else:
        now = datetime.utcnow().replace(microsecond=0)
        s = e = now
    record(db, user, "trigger_run", f"wf_id={wid}", project_id=pid)
    run = sched.create_run(db, wf, ver, "manual", s, e, triggered_by=user.id)
    db.commit()
    return _run_out(run)


@router.post("/workflows/{wid}/backfill")
def backfill(wid: int, body: BackfillIn, db=Depends(get_db),
             user=Depends(get_current_user), pid=Depends(get_project_id)):
    from croniter import croniter

    from ..services.scheduler import Scheduler

    wf = _wf_in_project(db, wid, pid)
    if not wf.cron:
        raise HTTPException(400, "未配置 Cron 的工作流不支持按区间补数")
    if body.end_date <= body.start_date:
        raise HTTPException(400, "补数区间非法")
    if body.parallel < 1:
        raise HTTPException(400, "并发度须 ≥1")
    ver = db.get(WorkflowVersion, wf.current_version_id)
    if ver is None:
        raise HTTPException(400, "工作流缺少版本")
    sched = Scheduler(None)
    it = croniter(wf.cron, body.start_date - timedelta(microseconds=1))
    a = it.get_next(datetime)
    pairs = []
    while True:
        b = it.get_next(datetime)
        if b > body.end_date:
            break
        pairs.append((a, b))
        a = b
    start = body.start_date
    end = body.end_date
    # 审计先于创建循环(同首个 create_run 事务提交):中途失败时审计计数可能多于实际创建数,
    # 有意取舍——审计语义为"发起了 x N 的补数请求"而非"成功创建 N 个实例"。
    record(db, user, "backfill", f"{start}~{end} x{len(pairs)}", project_id=pid)
    if not pairs:
        db.commit()  # 空区间也落审计
        return {"created": 0}
    created = 0
    for a, b in pairs:
        dup = db.scalar(select(WorkflowRun.id).where(
            WorkflowRun.workflow_id == wf.id, WorkflowRun.run_type == "backfill",
            WorkflowRun.data_interval_start == a).limit(1))
        if dup is None:
            sched.create_run(db, wf, ver, "backfill", a, b,
                             triggered_by=user.id, parallel_degree=body.parallel)
            created += 1
    db.commit()
    return {"created": created}


@router.get("/workflows/{wid}/runs")
def list_runs(wid: int, db=Depends(get_db), pid=Depends(get_project_id)):
    _wf_in_project(db, wid, pid)
    rows = db.scalars(select(WorkflowRun).where(WorkflowRun.workflow_id == wid)
                      .order_by(WorkflowRun.id.desc()).limit(200)).all()
    return [_run_out(r) for r in rows]


@router.get("/runs/{rid}")
def run_detail(rid: int, db=Depends(get_db), pid=Depends(get_project_id)):
    run = _run_in_project(db, rid, pid)
    tis = db.scalars(select(TaskInstance).where(TaskInstance.run_id == rid)
                     .order_by(TaskInstance.task_key)).all()
    out = _run_out(run)
    out["tasks"] = [{"id": t.id, "task_key": t.task_key, "task_type": t.task_type,
                     "state": t.state, "try_number": t.try_number,
                     "max_tries": t.max_tries, "result_json": t.result_json,
                     "started_at": t.started_at.isoformat() if t.started_at else None,
                     "finished_at": t.finished_at.isoformat() if t.finished_at else None}
                    for t in tis]
    return out


@router.post("/runs/{rid}/stop")
def stop_run(rid: int, db=Depends(get_db), user=Depends(get_current_user),
             pid=Depends(get_project_id)):
    # 终止操作不产生告警推送(操作者本人在场);仅 failed/success 走 on_run_finished
    run = _run_in_project(db, rid, pid)
    if run.state != "running":
        raise HTTPException(400, "仅运行中的实例可终止")
    run.state = "stopped"
    run.finished_at = datetime.utcnow()
    for t in db.scalars(select(TaskInstance).where(TaskInstance.run_id == rid)):
        if t.state in ("none", "queued", "up_for_retry"):
            t.state = "skipped"
        # running 的任务由执行器回收时发现 run 已停止并强杀(executor._reap_processes)
    record(db, user, "stop_run", f"run_id={rid}", project_id=pid)
    db.commit()
    return {"ok": True}


@router.post("/runs/{rid}/retry")
def retry_run(rid: int, db=Depends(get_db), user=Depends(get_current_user),
              pid=Depends(get_project_id)):
    run = _run_in_project(db, rid, pid)
    if run.state not in ("failed", "stopped"):
        raise HTTPException(400, "仅失败或已终止的实例可重跑")
    tis = db.scalars(select(TaskInstance).where(TaskInstance.run_id == rid)).all()
    if any(t.state == "running" for t in tis):
        raise HTTPException(400, "存在仍在运行的任务,请等待执行器回收后再重跑")
    for t in tis:
        if t.state != "success":  # 失败点续跑:成功任务保留
            t.state = "none"
            t.try_number = 0
            t.finished_at = None
            t.result_json = None
    run.state = "running"
    run.finished_at = None
    record(db, user, "retry_run", f"run_id={rid}", project_id=pid)
    db.commit()
    return {"ok": True}


@router.post("/tasks/{tid}/mark-success")
def mark_success(tid: int, db=Depends(get_db), user=Depends(get_current_user),
                 pid=Depends(get_project_id)):
    ti = db.get(TaskInstance, tid)
    if ti is None:
        raise HTTPException(404, "任务实例不存在")
    run = _run_in_project(db, ti.run_id, pid)
    if ti.state == "running":
        raise HTTPException(400, "运行中的任务不能置成功,请先终止实例")
    ti.state = "success"
    ti.finished_at = datetime.utcnow()
    # 运行中的 run 上对 failed/skipped 任务置成功是允许的(failure_policy=continue 场景),
    # 此时 run 仍在调度推进,无需复活;仅失败/已终止的 run 需要复活以重新判定后续。
    if run.state in ("failed", "stopped"):
        run.state = "running"  # 复活实例让调度器重新判定后续
        run.finished_at = None
    record(db, user, "mark_success", f"task_id={tid}", project_id=pid)
    db.commit()
    return {"ok": True}


@router.get("/tasks/{tid}/log")
def task_log(tid: int, db=Depends(get_db), pid=Depends(get_project_id)):
    ti = db.get(TaskInstance, tid)
    if ti is None:
        raise HTTPException(404, "任务实例不存在")
    _run_in_project(db, ti.run_id, pid)
    if not ti.log_path or not Path(ti.log_path).exists():
        raise HTTPException(404, "日志不存在")
    return PlainTextResponse(Path(ti.log_path).read_text(encoding="utf-8"))
