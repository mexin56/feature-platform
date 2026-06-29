"""调度内核:tick = Cron 水位调度 → 依赖推进 → 孤儿清理。
所有决策由 DB 状态推导(crash-safe);时钟可注入便于测试。
时间口径:data_interval 为工作流时区的 naive 时间;内部比较用 naive UTC。"""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from ..models import Alert, TaskInstance, Workflow, WorkflowRun, WorkflowVersion

HEARTBEAT_TIMEOUT_SEC = 60
TERMINAL_STATES = ("success", "failed", "upstream_failed", "skipped")


class Scheduler:
    SLA_THROTTLE_SEC = 60
    LAG_THROTTLE_SEC = 600

    def __init__(self, SessionLocal, settings=None, now_fn=None):
        self.SessionLocal = SessionLocal
        self.settings = settings
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._last_sla_check: datetime | None = None
        self._last_lag_check: datetime | None = None

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
        db.refresh(run)
        if run.state != "running":  # 期间被 stop 等外部操作改写 → 本 tick 不再推进
            return
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
            from .alerts import on_run_finished

            if wf is not None:
                on_run_finished(db, wf, run)

    # ---- ③ 孤儿清理 ----
    def reap_orphans(self) -> None:
        with self.SessionLocal() as db:
            now = self._now_utc()
            deadline = now - timedelta(seconds=HEARTBEAT_TIMEOUT_SEC)
            from sqlalchemy import and_, or_

            orphans = db.scalars(select(TaskInstance).where(
                TaskInstance.state == "running",
                or_(
                    and_(TaskInstance.heartbeat_at.isnot(None),
                         TaskInstance.heartbeat_at < deadline),
                    # 心跳从未写入(子进程在首跳前死亡)→ 按抢占时间兜底
                    and_(TaskInstance.heartbeat_at.is_(None),
                         TaskInstance.started_at.isnot(None),
                         TaskInstance.started_at < deadline),
                ))).all()
            for ti in orphans:
                ti.state = "up_for_retry" if ti.try_number < ti.max_tries else "failed"
                ti.finished_at = now  # 重试延迟基准
            db.commit()

    # ---- ④ SLA 检查(60s 节流;当天去重;判定口径:SLA 时刻后,昨天区间的
    # scheduled run 应已 success——即"今天 HH:MM 前应完成昨日数据加工") ----
    def check_sla(self) -> None:
        now = self._now_utc()
        if (self._last_sla_check is not None
                and (now - self._last_sla_check).total_seconds() < self.SLA_THROTTLE_SEC):
            return
        self._last_sla_check = now
        from .alerts import emit

        with self.SessionLocal() as db:
            wfs = db.scalars(select(Workflow).where(
                Workflow.status == "online", Workflow.sla_time.isnot(None))).all()
            for wf in wfs:
                now_local = self._now_local(wf.timezone)
                hh, mm = wf.sla_time.split(":")
                sla_today = now_local.replace(hour=int(hh), minute=int(mm),
                                              second=0, microsecond=0)
                if now_local < sla_today:
                    continue  # 今日 SLA 时刻未到
                day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
                # 去重窗口换算回 UTC(Alert.created_at 为 naive UTC):
                # now_utc - (now_local - day_start) = 本地今日零点对应的 UTC 时刻
                day_start_utc = now - (now_local - day_start)
                ok = db.scalar(select(WorkflowRun.id).where(
                    WorkflowRun.workflow_id == wf.id,
                    WorkflowRun.run_type == "scheduled",
                    WorkflowRun.state == "success",
                    WorkflowRun.data_interval_end >= day_start).limit(1))
                if ok:
                    continue
                dup = db.scalar(select(Alert.id).where(
                    Alert.kind == "sla_miss", Alert.workflow_id == wf.id,
                    Alert.created_at >= day_start_utc).limit(1))
                if dup:
                    continue
                emit(db, project_id=wf.project_id, level="error", kind="sla_miss",
                     title=f"工作流「{wf.name}」SLA 超时",
                     detail=f"应在 {wf.sla_time} 前完成当日调度,当前仍未成功",
                     workflow_id=wf.id)
            db.commit()

    # ---- ⑤ 物化滞后检查(600s 节流;当天去重;webhook 推送) ----
    def check_materialize_lag(self) -> None:
        now = self._now_utc()
        if (self._last_lag_check is not None
                and (now - self._last_lag_check).total_seconds() < self.LAG_THROTTLE_SEC):
            return
        self._last_lag_check = now
        from sqlalchemy import select as sa_select

        from ..models import FeatureGroup
        from .alerts import emit
        from .notify import get_setting

        with self.SessionLocal() as db:
            try:
                threshold = float(get_setting(db, "materialize_lag_hours", "24"))
            except ValueError:
                threshold = 24.0
            day_start_utc = now.replace(hour=0, minute=0, second=0, microsecond=0)
            fgs = db.scalars(sa_select(FeatureGroup).where(
                FeatureGroup.online_enabled.is_(True),
                FeatureGroup.materialize_watermark.isnot(None))).all()
            for fg in fgs:
                lag_hours = (now - fg.materialize_watermark).total_seconds() / 3600
                if lag_hours <= threshold:
                    continue
                dup = db.scalar(sa_select(Alert.id).where(
                    Alert.kind == "materialize_lag",
                    Alert.detail.like(f"fg_id={fg.id};%"),
                    Alert.created_at >= day_start_utc).limit(1))
                if dup:
                    continue
                emit(db, project_id=fg.project_id, level="warning", kind="materialize_lag",
                     title=f"特征组「{fg.name}」在线物化滞后",
                     detail=f"fg_id={fg.id};水位落后 {lag_hours:.1f} 小时(阈值 {threshold})",
                     workflow_id=fg.workflow_id)  # webhook 默认开启;当天去重防刷屏
            db.commit()

    # ---- tick 主循环 ----
    def tick(self) -> None:
        self.schedule_cron_runs()
        self.advance_runs()
        self.reap_orphans()
        self._drain_write_queue()
        self.check_sla()
        self.check_materialize_lag()

    def _drain_write_queue(self) -> None:
        """清理积压的写队列条目。"""
        if not self.settings:
            return
        from .collectors.writer_queue import pending_count, drain_queue
        cnt = pending_count(self.settings)
        if cnt:
            n = drain_queue(self.settings, max_batch=10)
            if n:
                import logging
                logging.getLogger("feature-platform").info(
                    f"scheduler drain 写队列 {n} 条(pending {cnt})")
