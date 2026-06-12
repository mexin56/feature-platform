import json
from datetime import datetime

from sqlalchemy import select

from backend.models import FeatureGroup, TaskInstance
from backend.services.scheduler import Scheduler
from backend.services.task_runner import run_task
from tests.test_scheduler_advance import _mk_run
from tests.test_scheduler_create import make_env, utc


def test_success_updates_bound_feature_group(tmp_path):
    Session, wf_id = make_env(tmp_path)
    with Session() as db:
        db.add(FeatureGroup(project_id=None, name="g", entity_keys_json='["k"]',
                            offline_kind="parquet", offline_location="g",
                            workflow_id=wf_id, task_key="t1"))
        db.commit()
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    with Session() as db:
        ti = db.scalar(select(TaskInstance).where(
            TaskInstance.run_id == rid, TaskInstance.task_key == "t1"))
        ti.state = "running"
        ti.try_number = 1
        ti.started_at = ti.heartbeat_at = datetime(2026, 6, 12)
        db.commit()
        tid = ti.id
    run_task(str(tmp_path / "meta.db"), tid, str(tmp_path))
    with Session() as db:
        fg = db.scalar(select(FeatureGroup))
    assert fg.last_produced_at is not None
    assert fg.last_produced_rows == 1  # duckdb select 1


def test_failure_does_not_update(tmp_path):
    Session, wf_id = make_env(tmp_path)
    with Session() as db:
        db.add(FeatureGroup(project_id=None, name="g", entity_keys_json='["k"]',
                            offline_kind="parquet", offline_location="g",
                            workflow_id=wf_id, task_key="t1"))
        db.commit()
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    with Session() as db:
        ti = db.scalar(select(TaskInstance).where(
            TaskInstance.run_id == rid, TaskInstance.task_key == "t1"))
        ti.params_json = json.dumps({"sql": "select * from ghost"})
        ti.state = "running"
        ti.try_number = 3  # 直接耗尽 → failed
        ti.started_at = ti.heartbeat_at = datetime(2026, 6, 12)
        db.commit()
        tid = ti.id
    run_task(str(tmp_path / "meta.db"), tid, str(tmp_path))
    with Session() as db:
        fg = db.scalar(select(FeatureGroup))
    assert fg.last_produced_at is None
