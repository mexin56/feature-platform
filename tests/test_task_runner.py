import json
from datetime import datetime

from sqlalchemy import select

from backend.models import TaskInstance
from backend.services.task_runner import run_task
from tests.test_scheduler_advance import _mk_run
from tests.test_scheduler_create import make_env, utc
from backend.services.scheduler import Scheduler

OK_DAG_SQL = "select 42 as answer"


def _claim(Session, run_id, key):
    """模拟执行器抢占:queued 前置状态由测试直接设置。"""
    with Session() as db:
        ti = db.scalar(select(TaskInstance).where(
            TaskInstance.run_id == run_id, TaskInstance.task_key == key))
        ti.state = "running"
        ti.try_number += 1
        ti.started_at = datetime(2026, 6, 12, 0, 0, 0)
        ti.heartbeat_at = ti.started_at
        db.commit()
        return ti.id


def test_run_task_success_writes_state_log_result(tmp_path):
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    tid = _claim(Session, rid, "t1")  # t1 是 duckdb_sql: select 1
    run_task(str(tmp_path / "meta.db"), tid, str(tmp_path))
    with Session() as db:
        ti = db.get(TaskInstance, tid)
    assert ti.state == "success"
    assert json.loads(ti.result_json)["rows"] == 1
    assert ti.finished_at is not None
    assert ti.log_path and (tmp_path / "logs") in __import__("pathlib").Path(ti.log_path).parents


def test_run_task_failure_retries_then_fails(tmp_path):
    """t1 max_tries=3:第 1/2 次失败→up_for_retry,第 3 次→failed;日志含 traceback。"""
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    with Session() as db:  # 把 t1 的 SQL 改坏
        ti = db.scalar(select(TaskInstance).where(
            TaskInstance.run_id == rid, TaskInstance.task_key == "t1"))
        ti.params_json = json.dumps({"sql": "select * from ghost_table"})
        db.commit()
        tid = ti.id
    for expect in ("up_for_retry", "up_for_retry", "failed"):
        _claim(Session, rid, "t1")
        run_task(str(tmp_path / "meta.db"), tid, str(tmp_path))
        with Session() as db:
            ti = db.get(TaskInstance, tid)
        assert ti.state == expect
    log = __import__("pathlib").Path(ti.log_path).read_text(encoding="utf-8")
    assert "ghost_table" in log  # traceback 落日志


def test_run_task_log_per_try(tmp_path):
    """每次尝试独立日志文件(文件名含 try 序号)。"""
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    tid = _claim(Session, rid, "t1")
    run_task(str(tmp_path / "meta.db"), tid, str(tmp_path))
    with Session() as db:
        assert "try1" in db.get(TaskInstance, tid).log_path
