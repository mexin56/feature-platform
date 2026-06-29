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
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "task_states": None}  # 列表页不含 task 明细


def _compute_state(run: WorkflowRun, db) -> None:
    """给列表页的 running 实例计算展示状态:
       - 所有 task 为 none/queued → 显示 'queued' (等待中,任务尚未开始)
       - 至少一个 task 为 running → 保持 'running'
    """
    if run.state != "running":
        return
    from ..models import TaskInstance
    from sqlalchemy import select, func
    cnt = db.scalar(
        select(func.count(TaskInstance.id))
        .where(TaskInstance.run_id == run.id, TaskInstance.state == "running"))
    if cnt == 0:
        run.state = "queued"  # 降级展示为"等待中"


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

    # 将补数区间转换到工作流时区的 naive 时间
    # note: cron 触发在 HH:MM,用户选的日期范围期望包含结束日当天的区间
    # 区间 = (前一个 cron 触发, 当前 cron 触发), 结束日的区间需要
    # end > 结束日的 cron 触发 + 1 个完整周期
    # 通用修复: end + 2 天确保覆盖,再按截止时间过滤多余区间
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(wf.timezone)
    start_local = body.start_date.astimezone(tz).replace(tzinfo=None)
    end_local_bare = body.end_date.astimezone(tz).replace(tzinfo=None)
    end_local = end_local_bare + timedelta(days=2)

    it = croniter(wf.cron, start_local - timedelta(microseconds=1))
    a = it.get_next(datetime)
    pairs = []
    while True:
        b = it.get_next(datetime)
        if b > end_local:
            break
        pairs.append((a, b))
        a = b

    # 过滤:只保留区间结束(b)在截止时间之前的
    # 截止 = 结束日 + 1天 + 18h(确保涵盖任何 HH:MM 的 cron 触发)
    cutoff = end_local_bare + timedelta(days=1, hours=18)
    pairs = [(s, e) for s, e in pairs if e <= cutoff]
    start = body.start_date
    end = body.end_date
    record(db, user, "backfill", f"{start}~{end} x{len(pairs)}", project_id=pid)
    if not pairs:
        db.commit()
        return {"created": 0, "skipped": 0, "restarted": 0, "total": len(pairs)}
    created = 0
    skipped = 0
    restarted = 0
    for a, b in pairs:
        dup = db.scalar(select(WorkflowRun.id).where(
            WorkflowRun.workflow_id == wf.id, WorkflowRun.run_type == "backfill",
            WorkflowRun.data_interval_start == a).limit(1))
        if dup is None:
            sched.create_run(db, wf, ver, "backfill", a, b,
                             triggered_by=user.id, parallel_degree=body.parallel)
            created += 1
        else:
            old_run = db.get(WorkflowRun, dup)
            if old_run.state in ("success", "failed", "stopped"):
                # 已完结的旧实例 → 创建新实例重跑
                sched.create_run(db, wf, ver, "backfill", a, b,
                                 triggered_by=user.id, parallel_degree=body.parallel)
                restarted += 1
            else:
                # 还在运行/排队中 → 跳过,不要重复触发
                skipped += 1
    db.commit()
    return {"created": created, "skipped": skipped, "restarted": restarted, "total": len(pairs)}


@router.get("/runs")
def list_all_runs(
    workflow_id: int | None = None,
    state: str | None = None,
    run_type: str | None = None,
    db=Depends(get_db),
    pid=Depends(get_project_id),
):
    """跨工作流实例列表(项目范围)。viewer 可读。最多返回 200 条,最新优先。"""
    # 先拉取项目内所有工作流(避免 N+1)
    wf_rows = db.scalars(select(Workflow).where(Workflow.project_id == pid)).all()
    wf_dict = {wf.id: wf.name for wf in wf_rows}
    if not wf_dict:
        return []

    stmt = select(WorkflowRun).where(WorkflowRun.workflow_id.in_(wf_dict.keys()))
    if workflow_id is not None:
        stmt = stmt.where(WorkflowRun.workflow_id == workflow_id)
    if state is not None:
        stmt = stmt.where(WorkflowRun.state == state)
    if run_type is not None:
        stmt = stmt.where(WorkflowRun.run_type == run_type)
    stmt = stmt.order_by(WorkflowRun.id.desc()).limit(200)

    rows = db.scalars(stmt).all()
    result = []
    for r in rows:
        _compute_state(r, db)
        item = _run_out(r)
        item["workflow_name"] = wf_dict.get(r.workflow_id, "")
        result.append(item)
    return result


@router.get("/workflows/{wid}/runs")
def list_runs(wid: int, db=Depends(get_db), pid=Depends(get_project_id)):
    _wf_in_project(db, wid, pid)
    rows = db.scalars(select(WorkflowRun).where(WorkflowRun.workflow_id == wid)
                      .order_by(WorkflowRun.id.desc()).limit(200)).all()
    result = []
    for r in rows:
        _compute_state(r, db)
        result.append(_run_out(r))
    return result


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
    if run.state not in ("queued", "running"):
        raise HTTPException(400, "仅排队中或运行中的实例可终止")
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


@router.post("/runs/{rid}/mark-success")
def mark_success_run(rid: int, db=Depends(get_db), user=Depends(get_current_user),
                     pid=Depends(get_project_id)):
    """强制整个实例成功:将所有非 success 任务置成功,实例置成功。"""
    run = _run_in_project(db, rid, pid)
    if run.state == "success":
        raise HTTPException(400, "实例已成功")
    tis = db.scalars(select(TaskInstance).where(TaskInstance.run_id == rid)).all()
    if any(t.state == "running" for t in tis):
        raise HTTPException(400, "存在仍在运行的任务,请先终止实例")
    now = datetime.utcnow()
    for t in tis:
        if t.state != "success":
            t.state = "success"
            t.finished_at = now
    run.state = "success"
    run.finished_at = now
    record(db, user, "mark_success_run", f"run_id={rid}", project_id=pid)
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
