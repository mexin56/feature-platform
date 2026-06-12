from datetime import datetime

from sqlalchemy import select

from backend.models import Alert, Workflow, WorkflowRun
from backend.services.scheduler import Scheduler
from tests.test_scheduler_create import make_env, utc


def _prep(tmp_path, sla="03:00"):
    """online 工作流,cron 每日 02:00,SLA 03:00(上海时区)。"""
    Session, wf_id = make_env(tmp_path, cron="0 2 * * *")
    with Session() as db:
        wf = db.get(Workflow, wf_id)
        wf.status = "online"
        wf.sla_time = sla
        wf.created_at = datetime(2026, 6, 10, 0, 0)
        db.commit()
    return Session, wf_id


def test_sla_miss_alert(tmp_path):
    Session, wf_id = _prep(tmp_path)
    # 上海 2026-06-12 04:00(UTC 06-11 20:00):当日 scheduled run 不存在 → SLA 失守
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 11, 20))
    sched.check_sla()
    with Session() as db:
        a = db.scalar(select(Alert).where(Alert.kind == "sla_miss"))
    assert a is not None and a.workflow_id == wf_id


def test_sla_ok_when_run_success(tmp_path):
    Session, wf_id = _prep(tmp_path)
    with Session() as db:
        wf = db.get(Workflow, wf_id)
        db.add(WorkflowRun(workflow_id=wf_id, version_id=wf.current_version_id,
                           run_type="scheduled",
                           data_interval_start=datetime(2026, 6, 11, 2),
                           data_interval_end=datetime(2026, 6, 12, 2),
                           state="success"))
        db.commit()
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 11, 20))
    sched.check_sla()
    with Session() as db:
        assert db.scalar(select(Alert).where(Alert.kind == "sla_miss")) is None


def test_sla_not_due_yet(tmp_path):
    Session, wf_id = _prep(tmp_path)
    # 上海 02:30,SLA 03:00 未到
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 11, 18, 30))
    sched.check_sla()
    with Session() as db:
        assert db.scalar(select(Alert).where(Alert.kind == "sla_miss")) is None


def test_sla_dedup_same_day(tmp_path):
    Session, wf_id = _prep(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 11, 20))
    sched.check_sla()
    sched._last_sla_check = None  # 绕过节流再查一次
    sched.check_sla()
    with Session() as db:
        assert len(db.scalars(select(Alert).where(Alert.kind == "sla_miss")).all()) == 1


def test_sla_dedup_correct_across_timezones(tmp_path):
    """去重窗口按 UTC 锚点:上海时区下当天已有告警不重复产生(回归:曾因本地/UTC 混比导致每 tick 重复)。"""
    from backend.models import Alert
    Session, wf_id = _prep(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 11, 20))
    sched.check_sla()
    with Session() as db:
        assert len(db.scalars(select(Alert).where(Alert.kind == "sla_miss")).all()) == 1
    # 模拟同日稍后(上海 06-12 06:00 = UTC 06-11 22:00),绕过节流
    sched2 = Scheduler(Session, now_fn=lambda: utc(2026, 6, 11, 22))
    sched2.check_sla()
    with Session() as db:
        assert len(db.scalars(select(Alert).where(Alert.kind == "sla_miss")).all()) == 1


def test_sla_throttled(tmp_path):
    """60s 节流:同一 Scheduler 实例短间隔重复调用直接跳过。"""
    Session, wf_id = _prep(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 11, 20))
    sched.check_sla()
    with Session() as db:  # 删掉告警,验证节流期间不会重新产生
        db.query(Alert).delete()
        db.commit()
    sched.check_sla()  # 60s 内 → 节流跳过
    with Session() as db:
        assert db.scalar(select(Alert)) is None
