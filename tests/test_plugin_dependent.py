from datetime import datetime

import pytest
from sqlalchemy.orm import sessionmaker

from backend.config import Settings
from backend.db import Base, make_engine
from backend.models import Workflow, WorkflowRun, WorkflowVersion
from backend.services.plugins import get_plugin
from backend.services.templating import build_context

CTX = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))


def _env_with_target(tmp_path, target_state):
    s = Settings(storage_dir=str(tmp_path))
    s.ensure_dirs()
    engine = make_engine(s.db_path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        wf = Workflow(project_id=None, name="upstream")
        db.add(wf)
        db.flush()
        ver = WorkflowVersion(workflow_id=wf.id, version_no=1, dag_json="{}")
        db.add(ver)
        db.flush()
        wf.current_version_id = ver.id
        if target_state:
            db.add(WorkflowRun(workflow_id=wf.id, version_id=ver.id, run_type="scheduled",
                               data_interval_start=datetime(2026, 6, 11),
                               data_interval_end=datetime(2026, 6, 12),
                               state=target_state))
        db.commit()
        wid = wf.id
    engine.dispose()
    return s, wid


def test_dependent_satisfied(tmp_path):
    env, wid = _env_with_target(tmp_path, "success")
    fn = get_plugin("dependent")
    assert fn({"workflow_id": wid}, CTX, env) == {"satisfied": True}


def test_dependent_not_satisfied_raises(tmp_path):
    env, wid = _env_with_target(tmp_path, "running")
    fn = get_plugin("dependent")
    with pytest.raises(RuntimeError, match="依赖未满足"):
        fn({"workflow_id": wid}, CTX, env)


def test_dependent_no_run_raises(tmp_path):
    env, wid = _env_with_target(tmp_path, None)
    fn = get_plugin("dependent")
    with pytest.raises(RuntimeError, match="依赖未满足"):
        fn({"workflow_id": wid}, CTX, env)
