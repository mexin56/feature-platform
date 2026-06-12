"""调度内核:tick = Cron 水位调度 → 依赖推进 → 孤儿清理。
所有决策由 DB 状态推导(crash-safe);时钟可注入便于测试。
时间口径:data_interval 为工作流时区的 naive 时间;内部比较用 naive UTC。"""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

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

    # ---- ① Cron 水位调度 ----
    def schedule_cron_runs(self) -> None:
        from croniter import croniter  # noqa: F401 (imported for availability check)

        with self.SessionLocal() as db:
            wfs = db.scalars(select(Workflow).where(
                Workflow.status == "online", Workflow.cron.isnot(None))).all()
            for wf in wfs:
                self._schedule_one(db, wf)

    def _schedule_one(self, db, wf: Workflow) -> None:
        from croniter import croniter

        now_local = self._now_local(wf.timezone)
        # 锚点:水位(上次区间末)或 created_at 兜底;减 1 微秒使边界本身可被 get_next 取到
        anchor = wf.last_scheduled_at or wf.created_at
        it = croniter(wf.cron, anchor - timedelta(microseconds=1))
        a = it.get_next(datetime)
        pairs: list[tuple[datetime, datetime]] = []
        while True:
            b = it.get_next(datetime)
            if b > now_local:
                break
            pairs.append((a, b))
            a = b
        if not pairs:
            return
        if not wf.catchup:
            pairs = pairs[-1:]  # 只补最新完整区间,跳过的区间不再创建
        active_count = db.scalar(
            select(func.count()).select_from(WorkflowRun).where(
                WorkflowRun.workflow_id == wf.id,
                WorkflowRun.state == "running",
                WorkflowRun.run_type.in_(("scheduled", "manual"))))
        ver = db.get(WorkflowVersion, wf.current_version_id)
        assert ver is not None, f"工作流 {wf.id} 缺少当前版本"
        for s, e in pairs:
            if active_count >= wf.concurrency_limit:
                return  # 背压:不创建、不推水位,下个 tick 重试
            dup = db.scalar(select(WorkflowRun.id).where(
                WorkflowRun.workflow_id == wf.id,
                WorkflowRun.run_type == "scheduled",
                WorkflowRun.data_interval_start == s).limit(1))
            if dup is None:
                self.create_run(db, wf, ver, "scheduled", s, e)
                active_count += 1
            wf.last_scheduled_at = e
            db.commit()

    # ---- ② 依赖推进与完结 ----
    def advance_runs(self) -> None:
        with self.SessionLocal() as db:
            runs = db.scalars(select(WorkflowRun).where(WorkflowRun.state == "running")
                              .order_by(WorkflowRun.workflow_id,
                                        WorkflowRun.data_interval_start)).all()
            gated = self._gate(db, runs)
            for run in gated:
                self._advance_one(db, run)
            db.commit()

    def _gate(self, db, runs: list) -> list:
        """并发门控:scheduled/manual 按工作流 concurrency_limit;
        backfill 按该批 parallel_degree;均按区间顺序放行前 K 个。"""
        allowed: list = []
        groups: dict = {}
        for r in runs:
            kind = "backfill" if r.run_type == "backfill" else "normal"
            groups.setdefault((r.workflow_id, kind), []).append(r)
        for (wf_id, kind), rs in groups.items():
            rs.sort(key=lambda r: r.data_interval_start)
            if kind == "backfill":
                cap = max(1, rs[0].parallel_degree)
            else:
                wf = db.get(Workflow, wf_id)
                cap = max(1, wf.concurrency_limit if wf else 1)
            allowed.extend(rs[:cap])
        return allowed

    def _advance_one(self, db, run: WorkflowRun) -> None:
        from .dag import upstream_map

        ver = db.get(WorkflowVersion, run.version_id)
        assert ver is not None, f"实例 {run.id} 缺少版本快照"
        dag = json.loads(ver.dag_json)
        ups = upstream_map(dag)
        tis = {t.task_key: t for t in db.scalars(
            select(TaskInstance).where(TaskInstance.run_id == run.id).order_by(TaskInstance.task_key)).all()}
        now = self._now_utc()
        wf = db.get(Workflow, run.workflow_id)
        for key, ti in tis.items():
            if ti.state == "none":
                up = [tis[u].state for u in ups.get(key, []) if u in tis]
                if any(s in ("failed", "upstream_failed") for s in up):
                    ti.state = "upstream_failed"
                elif any(s == "skipped" for s in up):
                    ti.state = "skipped"
                elif all(s == "success" for s in up):
                    ti.state = "queued"
            elif ti.state == "up_for_retry":
                base = ti.finished_at or now
                if now >= base + timedelta(seconds=ti.retry_delay_sec):
                    ti.state = "queued"
        # abort 仅以 failed 触发:upstream_failed 必然源自同 run 内某个 failed(重试耗尽的终态),
        # 单独出现不可达;若加入触发条件,会在"强制置成功"恢复场景下误杀后续任务。
        if wf and wf.failure_policy == "abort" and any(
                t.state == "failed" for t in tis.values()):
            for t in tis.values():
                if t.state in ("none", "queued", "up_for_retry"):
                    t.state = "skipped"
        # 完结判定
        if all(t.state in TERMINAL_STATES for t in tis.values()):
            ok = all(t.state in ("success", "skipped") for t in tis.values())
            run.state = "success" if ok else "failed"
            run.finished_at = now
