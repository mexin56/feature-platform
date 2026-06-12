import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.db import Base, make_engine
from backend.models import TaskInstance, Workflow, WorkflowRun, WorkflowVersion
from backend.services.scheduler import Scheduler

DAG = {"nodes": [
    {"key": "t1", "type": "duckdb_sql", "params": {"sql": "select 1"},
     "retries": 2, "retry_delay_sec": 30, "timeout_sec": 600},
    {"key": "t2", "type": "python_script", "params": {"script": "x.py"}},
], "edges": [["t1", "t2"]]}


def make_env(tmp_path, cron="0 2 * * *", **wf_kw):
    engine = make_engine(tmp_path / "meta.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        wf = Workflow(project_id=None, name="wf", cron=cron, timezone="Asia/Shanghai", **wf_kw)
        db.add(wf)
        db.flush()
        ver = WorkflowVersion(workflow_id=wf.id, version_no=1,
                              dag_json=json.dumps(DAG), created_by=None)
        db.add(ver)
        db.flush()
        wf.current_version_id = ver.id
        db.commit()
        wf_id = wf.id
    return Session, wf_id


def utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_create_run_snapshots_tasks(tmp_path):
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    with Session() as db:
        wf = db.get(Workflow, wf_id)
        ver = db.get(WorkflowVersion, wf.current_version_id)
        run = sched.create_run(db, wf, ver, "manual",
                               datetime(2026, 6, 11), datetime(2026, 6, 12))
        tis = db.scalars(select(TaskInstance).where(TaskInstance.run_id == run.id)
                         .order_by(TaskInstance.task_key)).all()
    assert [t.task_key for t in tis] == ["t1", "t2"]
    assert tis[0].max_tries == 3          # retries=2 → 最多 3 次
    assert tis[0].retry_delay_sec == 30
    assert tis[0].timeout_sec == 600
    assert tis[1].max_tries == 1          # 未配置 retries
    assert json.loads(tis[0].params_json) == {"sql": "select 1"}
    assert all(t.state == "none" for t in tis)
    assert run.run_type == "manual" and run.state == "running"
    assert run.parallel_degree == 1


def test_create_run_atomic(tmp_path):
    """run 与全部 TI 单事务:提交前查不到任何半创建状态。"""
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    with Session() as db:
        wf = db.get(Workflow, wf_id)
        ver = db.get(WorkflowVersion, wf.current_version_id)
        sched.create_run(db, wf, ver, "manual",
                         datetime(2026, 6, 11), datetime(2026, 6, 12))
    with Session() as db:
        runs = db.scalars(select(WorkflowRun)).all()
        tis = db.scalars(select(TaskInstance)).all()
    assert len(runs) == 1 and len(tis) == 2  # 要么全有,不存在 run 无 TI
