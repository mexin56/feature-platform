from datetime import datetime, timedelta

from sqlalchemy import select

from backend.models import TaskInstance, Workflow, WorkflowVersion
from backend.services.scheduler import Scheduler
from tests.test_scheduler_advance import _mk_run, _states
from tests.test_scheduler_create import make_env, utc


def _force(Session, run_id, key, **fields):
    with Session() as db:
        ti = db.scalar(select(TaskInstance).where(
            TaskInstance.run_id == run_id, TaskInstance.task_key == key))
        for k, v in fields.items():
            setattr(ti, k, v)
        db.commit()


def test_orphan_requeued_when_tries_left(tmp_path):
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12, 12))
    rid = _mk_run(Session, sched, wf_id)
    stale = datetime(2026, 6, 12, 11, 0)  # 心跳停在 1 小时前(naive UTC)
    _force(Session, rid, "t1", state="running", try_number=1,
           heartbeat_at=stale, started_at=stale)
    sched.reap_orphans()
    assert _states(Session, rid)["t1"] == "up_for_retry"  # max_tries=3 还有机会


def test_orphan_failed_when_tries_exhausted(tmp_path):
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12, 12))
    rid = _mk_run(Session, sched, wf_id)
    stale = datetime(2026, 6, 12, 11, 0)
    _force(Session, rid, "t2", state="running", try_number=1,
           heartbeat_at=stale, started_at=stale)  # t2 max_tries=1
    sched.reap_orphans()
    assert _states(Session, rid)["t2"] == "failed"


def test_fresh_heartbeat_untouched(tmp_path):
    Session, wf_id = make_env(tmp_path)
    now = utc(2026, 6, 12, 12)
    sched = Scheduler(Session, now_fn=lambda: now)
    rid = _mk_run(Session, sched, wf_id)
    fresh = datetime(2026, 6, 12, 11, 59, 30)
    _force(Session, rid, "t1", state="running", try_number=1, heartbeat_at=fresh)
    sched.reap_orphans()
    assert _states(Session, rid)["t1"] == "running"


def test_retry_requeues_after_delay(tmp_path):
    """up_for_retry 在 retry_delay_sec 之后由 advance 重新入队。"""
    Session, wf_id = make_env(tmp_path)
    rid = None
    t0 = utc(2026, 6, 12, 12)
    sched0 = Scheduler(Session, now_fn=lambda: t0)
    rid = _mk_run(Session, sched0, wf_id)
    _force(Session, rid, "t1", state="up_for_retry", try_number=1,
           finished_at=datetime(2026, 6, 12, 12, 0, 0))
    sched0.advance_runs()  # 延迟 30s 未到
    assert _states(Session, rid)["t1"] == "up_for_retry"
    sched1 = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12, 12, 1))
    sched1.advance_runs()  # 60s 后(>30s)
    assert _states(Session, rid)["t1"] == "queued"


def test_tick_runs_all_phases(tmp_path):
    """tick() 串联三阶段且不抛异常。"""
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12, 12))
    _mk_run(Session, sched, wf_id)
    sched.tick()
    with Session() as db:
        assert db.scalar(select(TaskInstance).where(
            TaskInstance.state == "queued")) is not None


def test_orphan_null_heartbeat_reaped_by_started_at(tmp_path):
    """子进程在写首次心跳前死亡:heartbeat_at=NULL,按 started_at 兜底回收。"""
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12, 12))
    rid = _mk_run(Session, sched, wf_id)
    stale = datetime(2026, 6, 12, 11, 0)
    _force(Session, rid, "t1", state="running", try_number=1,
           heartbeat_at=None, started_at=stale)
    sched.reap_orphans()
    assert _states(Session, rid)["t1"] == "up_for_retry"
