from datetime import datetime

from sqlalchemy import select

from backend.models import TaskInstance, Workflow, WorkflowRun, WorkflowVersion
from backend.services.scheduler import Scheduler
from tests.test_scheduler_create import make_env, utc


def _mk_run(Session, sched, wf_id, run_type="manual", parallel_degree=1,
            interval=(datetime(2026, 6, 11), datetime(2026, 6, 12))):
    with Session() as db:
        wf = db.get(Workflow, wf_id)
        ver = db.get(WorkflowVersion, wf.current_version_id)
        run = sched.create_run(db, wf, ver, run_type, *interval,
                               parallel_degree=parallel_degree)
        return run.id


def _set(Session, run_id, key, state):
    with Session() as db:
        ti = db.scalar(select(TaskInstance).where(
            TaskInstance.run_id == run_id, TaskInstance.task_key == key))
        ti.state = state
        db.commit()


def _states(Session, run_id):
    with Session() as db:
        tis = db.scalars(select(TaskInstance).where(TaskInstance.run_id == run_id)).all()
        return {t.task_key: t.state for t in tis}


def _run_state(Session, run_id):
    with Session() as db:
        return db.get(WorkflowRun, run_id).state


def test_root_task_queued_then_downstream_waits(tmp_path):
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    sched.advance_runs()
    assert _states(Session, rid) == {"t1": "queued", "t2": "none"}


def test_downstream_queued_after_upstream_success(tmp_path):
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    sched.advance_runs()
    _set(Session, rid, "t1", "success")
    sched.advance_runs()
    assert _states(Session, rid)["t2"] == "queued"


def test_upstream_failed_propagates_and_run_fails(tmp_path):
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    sched.advance_runs()
    _set(Session, rid, "t1", "failed")
    sched.advance_runs()
    assert _states(Session, rid)["t2"] == "upstream_failed"
    assert _run_state(Session, rid) == "failed"


def test_skipped_propagates_and_run_succeeds(tmp_path):
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    sched.advance_runs()
    _set(Session, rid, "t1", "skipped")
    sched.advance_runs()
    assert _states(Session, rid)["t2"] == "skipped"
    assert _run_state(Session, rid) == "success"  # 全 success/skipped 视为成功


def test_abort_policy_skips_pending(tmp_path):
    Session, wf_id = make_env(tmp_path, failure_policy="abort")
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    sched.advance_runs()
    _set(Session, rid, "t1", "failed")
    sched.advance_runs()
    states = _states(Session, rid)
    assert states["t2"] in ("skipped", "upstream_failed")  # abort:不再推进
    assert _run_state(Session, rid) == "failed"


def test_run_success_when_all_done(tmp_path):
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    sched.advance_runs()
    _set(Session, rid, "t1", "success")
    _set(Session, rid, "t2", "success")
    sched.advance_runs()
    assert _run_state(Session, rid) == "success"
    with Session() as db:
        assert db.get(WorkflowRun, rid).finished_at is not None


def test_backfill_serial_gate(tmp_path):
    """补数 parallel_degree=1:第二个 run 在第一个完结前不推进。"""
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    r1 = _mk_run(Session, sched, wf_id, run_type="backfill",
                 interval=(datetime(2026, 6, 9), datetime(2026, 6, 10)))
    r2 = _mk_run(Session, sched, wf_id, run_type="backfill",
                 interval=(datetime(2026, 6, 10), datetime(2026, 6, 11)))
    sched.advance_runs()
    assert _states(Session, r1)["t1"] == "queued"
    assert _states(Session, r2) == {"t1": "none", "t2": "none"}  # 门外等待
    _set(Session, r1, "t1", "success")
    _set(Session, r1, "t2", "success")
    sched.advance_runs()  # r1 完结
    sched.advance_runs()  # r2 放行
    assert _states(Session, r2)["t1"] == "queued"
