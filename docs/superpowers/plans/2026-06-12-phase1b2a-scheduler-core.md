# Phase 1b-2a:调度内核服务层 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 调度内核纯服务层:实例创建(单事务)、Cron 水位调度(注入时钟/catchup/背压)、依赖推进与失败策略、重试延迟与孤儿清理。全部可同步测试,不含子进程执行器(1b-2b)。

**Architecture:** `Scheduler` 类持有 sessionmaker + settings + 可注入时钟 `now_fn`(测试传 lambda,生产默认 UTC now)。所有调度决策由 DB 状态推导,无内存定时器,crash-safe。时间口径:Cron 在工作流时区求值,`data_interval` 存该时区的 naive 时间(模板变量 `{{ ds }}` 即业务日期);重试/心跳等内部比较用 naive UTC。落实 1b-1 终审遗留:①run+全部 TaskInstance 单事务创建;②catchup 锚点 = last_scheduled_at 或 created_at 兜底;③读版本断言非 None。

**Tech Stack:** croniter、zoneinfo(stdlib)。

**约定:** 命令在 `D:\feature-platform` 下执行,Python 用 `D:/conda/envs/scpy310/python.exe`。开发分支 `feature/phase1b2a-scheduler-core`(从 main 切出)。

---

### Task 1: parallel_degree 列 + Scheduler 骨架与 create_run(单事务)

**Files:**
- Modify: `backend/models.py`(WorkflowRun 加 parallel_degree)
- Modify: `backend/app.py`(ensure_column 迁移)
- Create: `backend/services/scheduler.py`
- Create: `tests/__init__.py`(空文件——本计划测试有跨文件复用 `from tests.test_scheduler_create import make_env, utc`,需使 tests 成为包)
- Create: `tests/test_scheduler_create.py`

- [ ] **Step 1: 写失败测试**

`tests/test_scheduler_create.py`:

```python
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.db import Base, make_engine
from backend.models import TaskInstance, Workflow, WorkflowRun, WorkflowVersion
from backend.services.scheduler import Scheduler

DAG = {"nodes": [
    {"key": "t1", "type": "duckdb_sql", "params": {"sql": "select 1"},
     "retries": 2, "retry_delay_sec": 30, "timeout_sec": 600},
    {"key": "t2", "type": "python_script", "params": {"script": "x.py"}},
], "edges": [["t1", "t2"]]}


def make_env(tmp_path, cron="0 2 * * *", **wf_kw):
    engine = make_engine(tmp_path / "meta.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        wf = Workflow(project_id=1, name="wf", cron=cron, timezone="Asia/Shanghai", **wf_kw)
        db.add(wf)
        db.flush()
        ver = WorkflowVersion(workflow_id=wf.id, version_no=1,
                              dag_json=json.dumps(DAG), created_by=None)
        db.add(ver)
        db.flush()
        wf.current_version_id = ver.id
        db.commit()
        wf_id = wf.id
    return Session, wf_id


def utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_create_run_snapshots_tasks(tmp_path):
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    with Session() as db:
        wf = db.get(Workflow, wf_id)
        ver = db.get(WorkflowVersion, wf.current_version_id)
        run = sched.create_run(db, wf, ver, "manual",
                               datetime(2026, 6, 11), datetime(2026, 6, 12))
        tis = db.scalars(select(TaskInstance).where(TaskInstance.run_id == run.id)
                         .order_by(TaskInstance.task_key)).all()
    assert [t.task_key for t in tis] == ["t1", "t2"]
    assert tis[0].max_tries == 3          # retries=2 → 最多 3 次
    assert tis[0].retry_delay_sec == 30
    assert tis[0].timeout_sec == 600
    assert tis[1].max_tries == 1          # 未配置 retries
    assert json.loads(tis[0].params_json) == {"sql": "select 1"}
    assert all(t.state == "none" for t in tis)
    assert run.run_type == "manual" and run.state == "running"
    assert run.parallel_degree == 1


def test_create_run_atomic(tmp_path):
    """run 与全部 TI 单事务:提交前查不到任何半创建状态。"""
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    with Session() as db:
        wf = db.get(Workflow, wf_id)
        ver = db.get(WorkflowVersion, wf.current_version_id)
        sched.create_run(db, wf, ver, "manual",
                         datetime(2026, 6, 11), datetime(2026, 6, 12))
    with Session() as db:
        runs = db.scalars(select(WorkflowRun)).all()
        tis = db.scalars(select(TaskInstance)).all()
    assert len(runs) == 1 and len(tis) == 2  # 要么全有,不存在 run 无 TI
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_scheduler_create.py -v`
Expected: FAIL,`ModuleNotFoundError`(scheduler 不存在)

- [ ] **Step 3: 写实现**

`backend/models.py` WorkflowRun 内 `finished_at` 之后追加一行字段:

```python
    parallel_degree: Mapped[int] = mapped_column(Integer, default=1)  # 补数批次并发度
```

`backend/app.py` 在 `Base.metadata.create_all(engine)` 之后追加(兼容已有库):

```python
    from .db import ensure_column

    ensure_column(engine, "workflow_runs", "parallel_degree", "INTEGER DEFAULT 1")
```

`backend/services/scheduler.py`:

```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_scheduler_create.py tests/ -v`
Expected: 新增 2 个 PASS,全量 63 passed

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/app.py backend/services/scheduler.py tests/__init__.py tests/test_scheduler_create.py
git commit -m "feat: Scheduler 骨架与 create_run 单事务实例创建"
```

注意:创建 `tests/__init__.py` 后先全量跑一遍 `tests/` 确认既有 61 个测试不受包化影响。

---

### Task 2: Cron 水位调度(catchup / 背压 / 重启不重不丢)

**Files:**
- Modify: `backend/services/scheduler.py`(追加 schedule_cron_runs)
- Create: `tests/test_scheduler_cron.py`

- [ ] **Step 1: 写失败测试**

`tests/test_scheduler_cron.py`:

```python
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
    Session, wf_id = make_env(tmp_path, cron="0 2 * * *")
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
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_scheduler_cron.py -v`
Expected: FAIL,`AttributeError: schedule_cron_runs`

- [ ] **Step 3: 写实现**

`backend/services/scheduler.py` Scheduler 类内追加:

```python
    # ---- ① Cron 水位调度 ----
    def schedule_cron_runs(self) -> None:
        from croniter import croniter

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
        active = db.scalar(
            select(WorkflowRun.id).where(
                WorkflowRun.workflow_id == wf.id, WorkflowRun.state == "running",
                WorkflowRun.run_type.in_(("scheduled", "manual"))).limit(1))
        active_count = db.query(WorkflowRun).filter(
            WorkflowRun.workflow_id == wf.id, WorkflowRun.state == "running",
            WorkflowRun.run_type.in_(("scheduled", "manual"))).count()
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
```

注意:`create_run` 内部已 commit;水位提交放在每个区间之后,崩溃最多重查一次 dup,不重不丢。实现后删除未使用的 `active` 变量(上面伪代码留了一行冗余,实现时只保留 `active_count` 统计)。

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_scheduler_cron.py tests/ -v`
Expected: 新增 5 个 PASS,全量 68 passed

- [ ] **Step 5: Commit**

```bash
git add backend/services/scheduler.py tests/test_scheduler_cron.py
git commit -m "feat: Cron 水位调度(catchup/背压/重启幂等)"
```

---

### Task 3: 依赖推进、失败传染、abort 策略、完结判定与补数门控

**Files:**
- Modify: `backend/services/scheduler.py`(追加 advance_runs)
- Create: `tests/test_scheduler_advance.py`

- [ ] **Step 1: 写失败测试**

`tests/test_scheduler_advance.py`:

```python
from datetime import datetime

from sqlalchemy import select

from backend.models import TaskInstance, Workflow, WorkflowRun, WorkflowVersion
from backend.services.scheduler import Scheduler
from tests.test_scheduler_create import make_env, utc


def _mk_run(Session, sched, wf_id, run_type="manual", parallel_degree=1,
            interval=(datetime(2026, 6, 11), datetime(2026, 6, 12))):
    with Session() as db:
        wf = db.get(Workflow, wf_id)
        ver = db.get(WorkflowVersion, wf.current_version_id)
        run = sched.create_run(db, wf, ver, run_type, *interval,
                               parallel_degree=parallel_degree)
        return run.id


def _set(Session, run_id, key, state):
    with Session() as db:
        ti = db.scalar(select(TaskInstance).where(
            TaskInstance.run_id == run_id, TaskInstance.task_key == key))
        ti.state = state
        db.commit()


def _states(Session, run_id):
    with Session() as db:
        tis = db.scalars(select(TaskInstance).where(TaskInstance.run_id == run_id)).all()
        return {t.task_key: t.state for t in tis}


def _run_state(Session, run_id):
    with Session() as db:
        return db.get(WorkflowRun, run_id).state


def test_root_task_queued_then_downstream_waits(tmp_path):
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    sched.advance_runs()
    assert _states(Session, rid) == {"t1": "queued", "t2": "none"}


def test_downstream_queued_after_upstream_success(tmp_path):
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    sched.advance_runs()
    _set(Session, rid, "t1", "success")
    sched.advance_runs()
    assert _states(Session, rid)["t2"] == "queued"


def test_upstream_failed_propagates_and_run_fails(tmp_path):
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    sched.advance_runs()
    _set(Session, rid, "t1", "failed")
    sched.advance_runs()
    assert _states(Session, rid)["t2"] == "upstream_failed"
    assert _run_state(Session, rid) == "failed"


def test_skipped_propagates_and_run_succeeds(tmp_path):
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    sched.advance_runs()
    _set(Session, rid, "t1", "skipped")
    sched.advance_runs()
    assert _states(Session, rid)["t2"] == "skipped"
    assert _run_state(Session, rid) == "success"  # 全 success/skipped 视为成功


def test_abort_policy_skips_pending(tmp_path):
    Session, wf_id = make_env(tmp_path, failure_policy="abort")
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    sched.advance_runs()
    _set(Session, rid, "t1", "failed")
    sched.advance_runs()
    states = _states(Session, rid)
    assert states["t2"] in ("skipped", "upstream_failed")  # abort:不再推进
    assert _run_state(Session, rid) == "failed"


def test_run_success_when_all_done(tmp_path):
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    sched.advance_runs()
    _set(Session, rid, "t1", "success")
    _set(Session, rid, "t2", "success")
    sched.advance_runs()
    assert _run_state(Session, rid) == "success"
    with Session() as db:
        assert db.get(WorkflowRun, rid).finished_at is not None


def test_backfill_serial_gate(tmp_path):
    """补数 parallel_degree=1:第二个 run 在第一个完结前不推进。"""
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    r1 = _mk_run(Session, sched, wf_id, run_type="backfill",
                 interval=(datetime(2026, 6, 9), datetime(2026, 6, 10)))
    r2 = _mk_run(Session, sched, wf_id, run_type="backfill",
                 interval=(datetime(2026, 6, 10), datetime(2026, 6, 11)))
    sched.advance_runs()
    assert _states(Session, r1)["t1"] == "queued"
    assert _states(Session, r2) == {"t1": "none", "t2": "none"}  # 门外等待
    _set(Session, r1, "t1", "success")
    _set(Session, r1, "t2", "success")
    sched.advance_runs()  # r1 完结
    sched.advance_runs()  # r2 放行
    assert _states(Session, r2)["t1"] == "queued"
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_scheduler_advance.py -v`
Expected: FAIL,`AttributeError: advance_runs`

- [ ] **Step 3: 写实现**

`backend/services/scheduler.py` Scheduler 类内追加:

```python
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

    def _gate(self, db, runs: list[WorkflowRun]) -> list[WorkflowRun]:
        """并发门控:scheduled/manual 按工作流 concurrency_limit;
        backfill 按该批 parallel_degree;均按区间顺序放行前 K 个。"""
        allowed: list[WorkflowRun] = []
        groups: dict[tuple, list[WorkflowRun]] = {}
        for r in runs:
            kind = "backfill" if r.run_type == "backfill" else "normal"
            groups.setdefault((r.workflow_id, kind), []).append(r)
        for (wf_id, kind), rs in groups.items():
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
            select(TaskInstance).where(TaskInstance.run_id == run.id)).all()}
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
        # abort 策略:出现 failed 即跳过所有未开始/待重试任务
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
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_scheduler_advance.py tests/ -v`
Expected: 新增 7 个 PASS,全量 75 passed

- [ ] **Step 5: Commit**

```bash
git add backend/services/scheduler.py tests/test_scheduler_advance.py
git commit -m "feat: 依赖推进/失败传染/abort 策略/完结判定/补数门控"
```

---

### Task 4: 重试延迟与孤儿清理

**Files:**
- Modify: `backend/services/scheduler.py`(追加 reap_orphans 与 tick)
- Create: `tests/test_scheduler_reap.py`

- [ ] **Step 1: 写失败测试**

`tests/test_scheduler_reap.py`:

```python
from datetime import datetime, timedelta

from sqlalchemy import select

from backend.models import TaskInstance, Workflow, WorkflowVersion
from backend.services.scheduler import Scheduler
from tests.test_scheduler_advance import _mk_run, _states
from tests.test_scheduler_create import make_env, utc


def _force(Session, run_id, key, **fields):
    with Session() as db:
        ti = db.scalar(select(TaskInstance).where(
            TaskInstance.run_id == run_id, TaskInstance.task_key == key))
        for k, v in fields.items():
            setattr(ti, k, v)
        db.commit()


def test_orphan_requeued_when_tries_left(tmp_path):
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12, 12))
    rid = _mk_run(Session, sched, wf_id)
    stale = datetime(2026, 6, 12, 11, 0)  # 心跳停在 1 小时前(naive UTC)
    _force(Session, rid, "t1", state="running", try_number=1,
           heartbeat_at=stale, started_at=stale)
    sched.reap_orphans()
    assert _states(Session, rid)["t1"] == "up_for_retry"  # max_tries=3 还有机会


def test_orphan_failed_when_tries_exhausted(tmp_path):
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12, 12))
    rid = _mk_run(Session, sched, wf_id)
    stale = datetime(2026, 6, 12, 11, 0)
    _force(Session, rid, "t2", state="running", try_number=1,
           heartbeat_at=stale, started_at=stale)  # t2 max_tries=1
    sched.reap_orphans()
    assert _states(Session, rid)["t2"] == "failed"


def test_fresh_heartbeat_untouched(tmp_path):
    Session, wf_id = make_env(tmp_path)
    now = utc(2026, 6, 12, 12)
    sched = Scheduler(Session, now_fn=lambda: now)
    rid = _mk_run(Session, sched, wf_id)
    fresh = datetime(2026, 6, 12, 11, 59, 30)
    _force(Session, rid, "t1", state="running", try_number=1, heartbeat_at=fresh)
    sched.reap_orphans()
    assert _states(Session, rid)["t1"] == "running"


def test_retry_requeues_after_delay(tmp_path):
    """up_for_retry 在 retry_delay_sec 之后由 advance 重新入队。"""
    Session, wf_id = make_env(tmp_path)
    rid = None
    t0 = utc(2026, 6, 12, 12)
    sched0 = Scheduler(Session, now_fn=lambda: t0)
    rid = _mk_run(Session, sched0, wf_id)
    _force(Session, rid, "t1", state="up_for_retry", try_number=1,
           finished_at=datetime(2026, 6, 12, 12, 0, 0))
    sched0.advance_runs()  # 延迟 30s 未到
    assert _states(Session, rid)["t1"] == "up_for_retry"
    sched1 = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12, 12, 1))
    sched1.advance_runs()  # 60s 后(>30s)
    assert _states(Session, rid)["t1"] == "queued"


def test_tick_runs_all_phases(tmp_path):
    """tick() 串联三阶段且不抛异常。"""
    Session, wf_id = make_env(tmp_path)
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12, 12))
    _mk_run(Session, sched, wf_id)
    sched.tick()
    with Session() as db:
        assert db.scalar(select(TaskInstance).where(
            TaskInstance.state == "queued")) is not None
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_scheduler_reap.py -v`
Expected: FAIL,`AttributeError: reap_orphans`

- [ ] **Step 3: 写实现**

`backend/services/scheduler.py` Scheduler 类内追加:

```python
    # ---- ③ 孤儿清理 ----
    def reap_orphans(self) -> None:
        with self.SessionLocal() as db:
            now = self._now_utc()
            deadline = now - timedelta(seconds=HEARTBEAT_TIMEOUT_SEC)
            orphans = db.scalars(select(TaskInstance).where(
                TaskInstance.state == "running",
                TaskInstance.heartbeat_at.isnot(None),
                TaskInstance.heartbeat_at < deadline)).all()
            for ti in orphans:
                ti.state = "up_for_retry" if ti.try_number < ti.max_tries else "failed"
                ti.finished_at = now  # 重试延迟基准
            db.commit()

    # ---- tick 主循环 ----
    def tick(self) -> None:
        self.schedule_cron_runs()
        self.advance_runs()
        self.reap_orphans()
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_scheduler_reap.py tests/ -v`
Expected: 新增 5 个 PASS,全量 80 passed

- [ ] **Step 5: Commit**

```bash
git add backend/services/scheduler.py tests/test_scheduler_reap.py
git commit -m "feat: 孤儿清理与重试延迟入队,tick 串联三阶段"
```

---

### Task 5: 全量回归与收尾

- [ ] **Step 1:** Run `D:/conda/envs/scpy310/python.exe -m pytest tests/ -v` → 全部 PASS(约 80 个)
- [ ] **Step 2:** Run `D:/conda/envs/scpy310/python.exe -c "from backend.app import create_app; app = create_app(); print('routes:', len(app.routes))"` → 正常(ensure_column 对既有库生效)
- [ ] **Step 3:** `git status --short` → 空

---

## Self-Review 记录

- **Spec 覆盖**:spec §5 调度内核的 ①Cron 水位 ②依赖推进 ③孤儿清理全部落地;执行器/插件/Runs API 归 1b-2b。终审遗留三项全部落实(单事务、catchup 锚点 created_at 兜底、版本断言)。
- **占位符**:无;Task 2 Step 3 中标注了一处实现时应删除的冗余行,属明确指令而非占位。
- **类型一致性**:`upstream_map`/`TERMINAL_STATES`/字段名与 1b-1 模型一致;测试复用 `make_env`/`utc` 跨文件 import(tests 包内合法);`parallel_degree` 在模型与 ensure_column 两处同步。
