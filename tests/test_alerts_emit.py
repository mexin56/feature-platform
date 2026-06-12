import json
from datetime import datetime

from sqlalchemy import select

from backend.models import Alert, FeatureGroup, QualityRecord, TaskInstance, Workflow
from backend.services.scheduler import Scheduler
from backend.services.task_runner import run_task
from tests.test_scheduler_advance import _mk_run, _set
from tests.test_scheduler_create import make_env, utc


def test_run_failed_alert_emitted(tmp_path, monkeypatch):
    sent = []
    from backend.services import alerts

    monkeypatch.setattr(alerts, "_send", lambda db, title, text: sent.append(title))
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    sched.advance_runs()
    _set(Session, rid, "t1", "failed")
    sched.advance_runs()  # 传染 + 完结 → 告警
    with Session() as db:
        a = db.scalar(select(Alert).where(Alert.kind == "run_failed"))
    assert a is not None and a.run_id == rid and a.level == "error"
    assert sent and "失败" in sent[0]


def test_run_success_alert_only_when_enabled(tmp_path):
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    sched.advance_runs()
    _set(Session, rid, "t1", "success")
    _set(Session, rid, "t2", "success")
    sched.advance_runs()
    with Session() as db:
        assert db.scalar(select(Alert).where(Alert.kind == "run_success")) is None
        db.get(Workflow, wf_id).alert_on_success = True
        db.commit()
    rid2 = _mk_run(Session, sched, wf_id,
                   interval=(datetime(2026, 6, 12), datetime(2026, 6, 13)))
    sched.advance_runs()
    _set(Session, rid2, "t1", "success")
    _set(Session, rid2, "t2", "success")
    sched.advance_runs()
    with Session() as db:
        assert db.scalar(select(Alert).where(Alert.kind == "run_success")) is not None


def _bound_fg_and_success_t1(tmp_path, rows_sql="select 1"):
    """构造绑定特征组并成功执行 t1,返回 (Session, wf_id, fg_id, run_task 用参数)。"""
    Session, wf_id = make_env(tmp_path)
    with Session() as db:
        fg = FeatureGroup(project_id=None, name="g", entity_keys_json='["k"]',
                          offline_kind="parquet", offline_location="g",
                          workflow_id=wf_id, task_key="t1")
        db.add(fg)
        db.commit()
        fgid = fg.id
    return Session, wf_id, fgid


def _exec_t1(Session, sched, tmp_path, wf_id, rid):
    with Session() as db:
        ti = db.scalar(select(TaskInstance).where(
            TaskInstance.run_id == rid, TaskInstance.task_key == "t1"))
        ti.state = "running"
        ti.try_number = 1
        ti.started_at = ti.heartbeat_at = datetime(2026, 6, 12)
        db.commit()
        tid = ti.id
    run_task(str(tmp_path / "meta.db"), tid, str(tmp_path))


def test_quality_record_written_and_drop_alert(tmp_path):
    Session, wf_id, fgid = _bound_fg_and_success_t1(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    # 第一次:rows=3
    rid1 = _mk_run(Session, sched, wf_id)
    with Session() as db:
        ti = db.scalar(select(TaskInstance).where(
            TaskInstance.run_id == rid1, TaskInstance.task_key == "t1"))
        ti.params_json = json.dumps({"sql": "select 1 union all select 2 union all select 3"})
        db.commit()
    _exec_t1(Session, sched, tmp_path, wf_id, rid1)
    with Session() as db:
        q = db.scalars(select(QualityRecord).where(
            QualityRecord.feature_group_id == fgid)).all()
    assert len(q) == 1 and q[0].rows == 3
    # 第二次:rows=1(降幅 66% > 50%)→ quality_drop 告警
    rid2 = _mk_run(Session, sched, wf_id,
                   interval=(datetime(2026, 6, 12), datetime(2026, 6, 13)))
    _exec_t1(Session, sched, tmp_path, wf_id, rid2)
    with Session() as db:
        a = db.scalar(select(Alert).where(Alert.kind == "quality_drop"))
        qs = db.scalars(select(QualityRecord)).all()
    assert len(qs) == 2
    assert a is not None and "g" in a.title


def test_quality_drop_pushes_sync_in_child(tmp_path, monkeypatch):
    """质量突变推送走同步通道(_post_card),不依赖守护线程。"""
    posted = []
    from backend.services import notify, task_runner

    monkeypatch.setattr(notify, "_post_card", lambda url, t, d: posted.append(t))
    Session, wf_id, fgid = _bound_fg_and_success_t1(tmp_path)
    from backend.models import SystemSetting

    with Session() as db:
        db.add(SystemSetting(key="webhook_url", value="https://hook"))
        db.commit()
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid1 = _mk_run(Session, sched, wf_id)
    with Session() as db:
        ti = db.scalar(select(TaskInstance).where(
            TaskInstance.run_id == rid1, TaskInstance.task_key == "t1"))
        ti.params_json = json.dumps({"sql": "select 1 union all select 2 union all select 3"})
        db.commit()
    _exec_t1(Session, sched, tmp_path, wf_id, rid1)
    rid2 = _mk_run(Session, sched, wf_id,
                   interval=(datetime(2026, 6, 12), datetime(2026, 6, 13)))
    with Session() as db:
        ti = db.scalar(select(TaskInstance).where(
            TaskInstance.run_id == rid2, TaskInstance.task_key == "t1"))
        ti.params_json = json.dumps({"sql": "select 1"})
        db.commit()
    _exec_t1(Session, sched, tmp_path, wf_id, rid2)
    assert any("突降" in t for t in posted)
