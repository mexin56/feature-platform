from datetime import datetime

from sqlalchemy.orm import sessionmaker

from backend.db import Base, make_engine
from backend.models import (
    TaskInstance, User, Workflow, WorkflowRun, WorkflowVersion,
)


def _session(tmp_path):
    engine = make_engine(tmp_path / "meta.db")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_workflow_version_run_taskinstance(tmp_path):
    Session = _session(tmp_path)
    with Session() as db:
        u = User(username="a", password_hash="x", role="developer")
        db.add(u)
        db.flush()
        wf = Workflow(project_id=None, name="日特征", cron="0 2 * * *", created_by=u.id)
        db.add(wf)
        db.flush()
        ver = WorkflowVersion(workflow_id=wf.id, version_no=1, dag_json="{}", created_by=u.id)
        db.add(ver)
        db.flush()
        wf.current_version_id = ver.id
        run = WorkflowRun(workflow_id=wf.id, version_id=ver.id, run_type="scheduled",
                          data_interval_start=datetime(2026, 6, 11),
                          data_interval_end=datetime(2026, 6, 12))
        db.add(run)
        db.flush()
        ti = TaskInstance(run_id=run.id, task_key="t1", task_type="duckdb_sql",
                          params_json="{}", max_tries=3)
        db.add(ti)
        db.commit()
        assert db.query(Workflow).one().status == "offline"
        assert db.query(Workflow).one().failure_policy == "continue"
        assert db.query(WorkflowRun).one().state == "running"
        got = db.query(TaskInstance).one()
        assert got.state == "none"
        assert got.try_number == 0
        assert got.max_tries == 3


def test_workflow_name_unique_per_project(tmp_path):
    import pytest
    from sqlalchemy.exc import IntegrityError

    Session = _session(tmp_path)
    with Session() as db:
        # Create user and project first
        from backend.models import Project
        u = User(username="owner", password_hash="x", role="developer")
        db.add(u)
        db.flush()
        p = Project(name="p1", owner_id=u.id)
        db.add(p)
        db.flush()

        db.add(Workflow(project_id=p.id, name="w"))
        db.commit()
        db.add(Workflow(project_id=p.id, name="w"))
        with pytest.raises(IntegrityError):
            db.commit()
