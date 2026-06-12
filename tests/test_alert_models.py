from sqlalchemy.orm import sessionmaker

from backend.db import Base, make_engine
from backend.models import Alert, QualityRecord, SystemSetting, Workflow


def _session(tmp_path):
    engine = make_engine(tmp_path / "meta.db")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_alert_defaults(tmp_path):
    Session = _session(tmp_path)
    with Session() as db:
        db.add(Alert(project_id=None, level="error", kind="run_failed",
                     title="工作流 wf 失败", detail="run_id=1"))
        db.commit()
        a = db.query(Alert).one()
        assert a.read is False and a.created_at is not None


def test_quality_record(tmp_path):
    from backend.models import FeatureGroup
    Session = _session(tmp_path)
    with Session() as db:
        fg = FeatureGroup(project_id=None, name="g", entity_keys_json='["k"]',
                          offline_kind="parquet", offline_location="g")
        db.add(fg)
        db.flush()
        db.add(QualityRecord(feature_group_id=fg.id, run_id=None, rows=100,
                             distinct_keys=98, null_ratio=0.01))
        db.commit()
        q = db.query(QualityRecord).one()
        assert q.rows == 100 and abs(q.null_ratio - 0.01) < 1e-9


def test_system_setting_kv(tmp_path):
    Session = _session(tmp_path)
    with Session() as db:
        db.add(SystemSetting(key="webhook_url", value="https://open.feishu.cn/x"))
        db.commit()
        assert db.query(SystemSetting).one().value.startswith("https://")


def test_workflow_alert_columns(tmp_path):
    Session = _session(tmp_path)
    with Session() as db:
        db.add(Workflow(project_id=None, name="w"))
        db.commit()
        w = db.query(Workflow).one()
        assert w.alert_on_failure is True
        assert w.alert_on_success is False
        assert w.sla_time is None
