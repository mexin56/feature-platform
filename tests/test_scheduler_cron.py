from datetime import datetime

from sqlalchemy import select

from backend.models import Workflow, WorkflowRun
from backend.services.scheduler import Scheduler
from tests.test_scheduler_create import make_env, utc


def _online(Session, wf_id):
    with Session() as db:
        db.get(Workflow, wf_id).status = "online"
        db.commit()


def _runs(Session):
    with Session() as db:
        return db.scalars(select(WorkflowRun).order_by(WorkflowRun.data_interval_start)).all()


def test_first_tick_creates_latest_interval_only(tmp_path):
    """catchup=False:首次调度只补最新一个完整区间(锚点=created_at)。"""
    Session, wf_id = make_env(tmp_path, cron="0 2 * * *")
    with Session() as db:
        db.get(Workflow, wf_id).created_at = datetime(2026, 6, 9, 10, 0)
        db.commit()
    _online(Session, wf_id)
    # 上海时间 2026-06-12 03:00 = UTC 2026-06-11 19:00
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 11, 19))
    sched.schedule_cron_runs()
    runs = _runs(Session)
    assert len(runs) == 1
    assert runs[0].data_interval_start == datetime(2026, 6, 11, 2, 0)
    assert runs[0].data_interval_end == datetime(2026, 6, 12, 2, 0)
    assert runs[0].run_type == "scheduled"
    with Session() as db:
        assert db.get(Workflow, wf_id).last_scheduled_at == datetime(2026, 6, 12, 2, 0)


def test_catchup_true_backfills_all(tmp_path):
    Session, wf_id = make_env(tmp_path, cron="0 2 * * *", catchup=True, concurrency_limit=10)
    with Session() as db:
        db.get(Workflow, wf_id).created_at = datetime(2026, 6, 9, 10, 0)
        db.commit()
    _online(Session, wf_id)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 11, 19))
    sched.schedule_cron_runs()
    runs = _runs(Session)
    # 区间边界:6-10 02:00 / 6-11 02:00 / 6-12 02:00 → 两个完整区间
    assert [(r.data_interval_start, r.data_interval_end) for r in runs] == [
        (datetime(2026, 6, 10, 2), datetime(2026, 6, 11, 2)),
        (datetime(2026, 6, 11, 2), datetime(2026, 6, 12, 2)),
    ]


def test_retick_no_duplicates_and_watermark_advances(tmp_path):
    Session, wf_id = make_env(tmp_path, cron="0 2 * * *", concurrency_limit=10)
    with Session() as db:
        db.get(Workflow, wf_id).created_at = datetime(2026, 6, 9, 10, 0)
        db.commit()
    _online(Session, wf_id)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 11, 19))
    sched.schedule_cron_runs()
    sched.schedule_cron_runs()  # 同一时刻重复 tick(模拟重启)
    assert len(_runs(Session)) == 1
    # 时间推进一天后再 tick → 多一个区间
    sched2 = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12, 19))
    sched2.schedule_cron_runs()
    runs = _runs(Session)
    assert len(runs) == 2
    assert runs[1].data_interval_end == datetime(2026, 6, 13, 2, 0)


def test_offline_or_no_cron_not_scheduled(tmp_path):
    Session, wf_id = make_env(tmp_path, cron="0 2 * * *")  # 默认 offline
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 11, 19))
    sched.schedule_cron_runs()
    assert _runs(Session) == []


def test_backpressure_active_runs_at_limit(tmp_path):
    """活跃实例达 concurrency_limit:不再产新实例,水位不前进。"""
    Session, wf_id = make_env(tmp_path, cron="0 2 * * *", concurrency_limit=1)
    with Session() as db:
        db.get(Workflow, wf_id).created_at = datetime(2026, 6, 9, 10, 0)
        db.commit()
    _online(Session, wf_id)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 11, 19))
    sched.schedule_cron_runs()
    assert len(_runs(Session)) == 1  # 第一个实例(state=running 占用槽位)
    sched2 = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12, 19))
    sched2.schedule_cron_runs()
    assert len(_runs(Session)) == 1  # 背压:旧实例未完结,不产新实例
    with Session() as db:
        run = db.scalars(select(WorkflowRun)).one()
        run.state = "success"
        db.commit()
    sched2.schedule_cron_runs()
    assert len(_runs(Session)) == 2  # 释放后补上
