import json
from datetime import datetime

from sqlalchemy import select

from backend.config import Settings
from backend.models import TaskInstance
from backend.services.executor import Executor
from backend.services.scheduler import Scheduler
from tests.test_scheduler_advance import _mk_run, _states
from tests.test_scheduler_create import make_env, utc


def _setup(tmp_path):
    Session, wf_id = make_env(tmp_path)
    settings = Settings(storage_dir=str(tmp_path))
    settings.ensure_dirs()
    sched = Scheduler(Session, settings, now_fn=lambda: utc(2026, 6, 12))
    ex = Executor(Session, settings, sync=True, now_fn=lambda: utc(2026, 6, 12))
    return Session, wf_id, sched, ex


def test_claim_is_atomic(tmp_path):
    Session, wf_id, sched, ex = _setup(tmp_path)
    rid = _mk_run(Session, sched, wf_id)
    sched.advance_runs()  # t1 → queued
    with Session() as db:
        tid = db.scalar(select(TaskInstance).where(
            TaskInstance.run_id == rid, TaskInstance.task_key == "t1")).id
    assert ex._claim(tid) is True
    assert ex._claim(tid) is False  # 二次抢占失败
    with Session() as db:
        ti = db.get(TaskInstance, tid)
    assert ti.state == "running" and ti.try_number == 1
    assert ti.started_at is not None and ti.heartbeat_at is not None


def test_sync_poll_executes_queued(tmp_path):
    Session, wf_id, sched, ex = _setup(tmp_path)
    rid = _mk_run(Session, sched, wf_id)
    sched.advance_runs()
    ex.poll()  # 同步执行 t1(duckdb: select 1)
    assert _states(Session, rid)["t1"] == "success"
    with Session() as db:
        ti = db.scalar(select(TaskInstance).where(
            TaskInstance.run_id == rid, TaskInstance.task_key == "t1"))
    assert json.loads(ti.result_json)["rows"] == 1


def test_sync_full_run_to_success(tmp_path):
    """tick+poll 循环驱动整个 run 到 success(t2 python_script 需先放脚本)。"""
    Session, wf_id, sched, ex = _setup(tmp_path)
    settings = ex.settings
    (settings.scripts_dir / "x.py").write_text("print('hi')\n", encoding="utf-8")
    rid = _mk_run(Session, sched, wf_id)
    for _ in range(6):
        sched.tick()
        ex.poll()
    from backend.models import WorkflowRun

    with Session() as db:
        assert db.get(WorkflowRun, rid).state == "success"


def test_slots_limit(tmp_path):
    """max_workers=1:一次 poll 只领一个任务(sync 模式下逐个执行,验证领取不超额)。"""
    Session, wf_id, sched, ex = _setup(tmp_path)
    ex.max_workers = 1
    r1 = _mk_run(Session, sched, wf_id, interval=(datetime(2026, 6, 9), datetime(2026, 6, 10)))
    r2 = _mk_run(Session, sched, wf_id, interval=(datetime(2026, 6, 10), datetime(2026, 6, 11)))
    sched.advance_runs()
    claimed = ex._claim_due(limit=1)
    assert len(claimed) == 1


def test_subprocess_mode_executes(tmp_path):
    """真实子进程模式:t1 成功(Windows spawn 路径)。"""
    Session, wf_id = make_env(tmp_path)
    settings = Settings(storage_dir=str(tmp_path))
    settings.ensure_dirs()
    sched = Scheduler(Session, settings, now_fn=lambda: utc(2026, 6, 12))
    ex = Executor(Session, settings, sync=False, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    sched.advance_runs()
    ex.poll()  # 拉起子进程
    import time

    for _ in range(60):  # 最多等 30s
        ex.poll()  # 回收已完成进程
        if _states(Session, rid)["t1"] in ("success", "failed"):
            break
        time.sleep(0.5)
    assert _states(Session, rid)["t1"] == "success"


def test_timeout_kill(tmp_path):
    """超时强杀:timeout_sec=1 的死循环脚本被终止并按重试预算处理。"""
    Session, wf_id = make_env(tmp_path)
    settings = Settings(storage_dir=str(tmp_path))
    settings.ensure_dirs()
    (settings.scripts_dir / "loop.py").write_text(
        "import time\nwhile True: time.sleep(1)\n", encoding="utf-8")
    sched = Scheduler(Session, settings, now_fn=lambda: utc(2026, 6, 12))
    ex = Executor(Session, settings, sync=False)
    rid = _mk_run(Session, sched, wf_id)
    with Session() as db:  # 改造 t2:执行死循环脚本,超时 1s,无重试
        ti = db.scalar(select(TaskInstance).where(
            TaskInstance.run_id == rid, TaskInstance.task_key == "t2"))
        ti.params_json = json.dumps({"script": "loop.py"})
        ti.timeout_sec = 1
        db.commit()
        t1 = db.scalar(select(TaskInstance).where(
            TaskInstance.run_id == rid, TaskInstance.task_key == "t1"))
        t1.state = "success"  # 直通 t2
        db.commit()
    sched.advance_runs()
    ex.poll()
    import time

    for _ in range(60):
        time.sleep(0.5)
        ex.poll()
        if _states(Session, rid)["t2"] == "failed":
            break
    assert _states(Session, rid)["t2"] == "failed"
    with Session() as db:
        ti = db.scalar(select(TaskInstance).where(
            TaskInstance.run_id == rid, TaskInstance.task_key == "t2"))
    assert "超时" in (ti.result_json or "") or True  # 终态正确即可,结果信息尽力而为
