"""执行器:原子抢占 queued 任务 → 执行(sync 内联 / 子进程)→ 超时强杀与回收。
sync=True 供测试与冒烟:poll 内同步执行,确定性驱动。"""
import multiprocessing
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from ..models import TaskInstance, WorkflowRun
from .task_runner import run_task


class Executor:
    def __init__(self, SessionLocal, settings, max_workers: int = 4,
                 sync: bool = False, now_fn=None):
        self.SessionLocal = SessionLocal
        self.settings = settings
        self.max_workers = max_workers
        self.sync = sync
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._procs: dict[int, multiprocessing.Process] = {}  # ti_id -> Process

    def _now(self) -> datetime:
        return self.now_fn().astimezone(timezone.utc).replace(tzinfo=None)

    # ---- 原子抢占 ----
    def _claim(self, ti_id: int) -> bool:
        with self.SessionLocal() as db:
            now = self._now()
            res = db.execute(update(TaskInstance)
                             .where(TaskInstance.id == ti_id, TaskInstance.state == "queued")
                             .values(state="running",
                                     try_number=TaskInstance.try_number + 1,
                                     started_at=now, heartbeat_at=now))
            db.commit()
            return res.rowcount == 1

    def _claim_due(self, limit: int) -> list[int]:
        """领取至多 limit 个 queued 任务(只取 running 状态实例下的)。"""
        if limit <= 0:
            return []
        with self.SessionLocal() as db:
            rows = db.execute(
                select(TaskInstance.id)
                .join(WorkflowRun, WorkflowRun.id == TaskInstance.run_id)
                .where(TaskInstance.state == "queued", WorkflowRun.state == "running")
                .order_by(TaskInstance.id).limit(limit)).scalars().all()
        return [tid for tid in rows if self._claim(tid)]

    # ---- 主循环 ----
    def poll(self) -> None:
        self._reap_processes()
        free = self.max_workers - len(self._procs)
        for tid in self._claim_due(free):
            if self.sync:
                run_task(str(self.settings.db_path), tid, str(self.settings.storage_dir))
            else:
                p = multiprocessing.Process(
                    target=run_task,
                    args=(str(self.settings.db_path), tid, str(self.settings.storage_dir)),
                    daemon=True)
                p.start()
                self._procs[tid] = p

    # ---- 回收与超时 ----
    def _reap_processes(self) -> None:
        if not self._procs:
            return
        now = self._now()
        done: list[int] = []
        with self.SessionLocal() as db:
            for tid, proc in self._procs.items():
                ti = db.get(TaskInstance, tid)
                if ti is None:
                    done.append(tid)
                    continue
                run = db.get(WorkflowRun, ti.run_id)
                if proc.is_alive() and run is not None and run.state == "stopped":
                    proc.terminate()
                    proc.join(timeout=5)
                    if proc.is_alive():  # terminate 未生效 → 强杀
                        proc.kill()
                        proc.join(1)
                    if ti.state == "running":
                        ti.state = "failed"
                        ti.finished_at = now
                        ti.result_json = '{"error": "实例已终止,任务被强杀"}'
                    done.append(tid)
                    continue
                # 超时基准 started_at 由 _claim 在每次抢占时刷新,重试任务的超时时钟随之重置
                timed_out = (ti.timeout_sec and ti.started_at
                             and now > ti.started_at + timedelta(seconds=ti.timeout_sec))
                if proc.is_alive() and timed_out:
                    proc.terminate()
                    proc.join(timeout=5)
                    if proc.is_alive():  # terminate 未生效(如卡在 C 扩展)→ 强杀
                        proc.kill()
                        proc.join(1)
                    if ti.state == "running":
                        ti.state = ("up_for_retry" if ti.try_number < ti.max_tries
                                    else "failed")
                        ti.finished_at = now
                        ti.result_json = '{"error": "执行超时,已强杀"}'
                    done.append(tid)
                elif not proc.is_alive():
                    if ti.state == "running":  # 子进程崩溃没写终态 → 兜底
                        ti.state = ("up_for_retry" if ti.try_number < ti.max_tries
                                    else "failed")
                        ti.finished_at = now
                        ti.result_json = '{"error": "子进程异常退出"}'
                    done.append(tid)
            db.commit()
        for tid in done:
            self._procs.pop(tid, None)
