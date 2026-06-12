"""调度内核:tick = Cron 水位调度 → 依赖推进 → 孤儿清理。
所有决策由 DB 状态推导(crash-safe);时钟可注入便于测试。
时间口径:data_interval 为工作流时区的 naive 时间;内部比较用 naive UTC。"""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..models import TaskInstance, Workflow, WorkflowRun, WorkflowVersion

HEARTBEAT_TIMEOUT_SEC = 60
TERMINAL_STATES = ("success", "failed", "upstream_failed", "skipped")


class Scheduler:
    def __init__(self, SessionLocal, settings=None, now_fn=None):
        self.SessionLocal = SessionLocal
        self.settings = settings
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    # ---- 时钟 ----
    def _now_utc(self) -> datetime:
        """naive UTC,用于重试/心跳/finished_at 比较。"""
        return self.now_fn().astimezone(timezone.utc).replace(tzinfo=None)

    def _now_local(self, tz_name: str) -> datetime:
        """工作流时区的 naive 当前时间,用于 Cron 求值。"""
        from zoneinfo import ZoneInfo

        return self.now_fn().astimezone(ZoneInfo(tz_name)).replace(tzinfo=None)

    # ---- 实例创建(单事务) ----
    def create_run(self, db, wf: Workflow, ver: WorkflowVersion, run_type: str,
                   interval_start: datetime, interval_end: datetime,
                   triggered_by: int | None = None, parallel_degree: int = 1) -> WorkflowRun:
        assert ver is not None, "工作流缺少当前版本"
        dag = json.loads(ver.dag_json)
        run = WorkflowRun(workflow_id=wf.id, version_id=ver.id, run_type=run_type,
                          data_interval_start=interval_start, data_interval_end=interval_end,
                          triggered_by=triggered_by, parallel_degree=parallel_degree)
        db.add(run)
        db.flush()
        for n in dag["nodes"]:
            db.add(TaskInstance(
                run_id=run.id, task_key=n["key"], task_type=n["type"],
                params_json=json.dumps(n.get("params") or {}, ensure_ascii=False),
                max_tries=int(n.get("retries", 0)) + 1,
                retry_delay_sec=int(n.get("retry_delay_sec", 60)),
                timeout_sec=n.get("timeout_sec")))
        db.commit()  # run 与全部 TI 一并提交,杜绝半创建状态
        return run
