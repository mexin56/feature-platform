# Phase 3:监控、告警与加固 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 告警(运行失败/成功、SLA 超时、质量环比突变、物化滞后)+ Webhook(飞书卡片)+ 站内告警中心 + 监控大盘 API;并落实历史加固项(审计同事务、孙进程超时、在线批量查询)。

**Architecture:** 告警产生点内嵌在既有链路:run 完结(advance_runs)→ 失败/成功告警;生产注册(_register_production)→ 质量记录+环比突变;Scheduler 新增 check_sla 阶段(60s 节流+当天去重);物化滞后由大盘 API 惰性扫描(展示+落告警,当天去重)。Webhook 全局一条(system_settings KV 表,管理员配置),发送失败只记日志不影响主流程。

**约定:** 命令在 `D:\feature-platform`,Python `D:/conda/envs/scpy310/python.exe`。分支 `feature/phase3-monitoring`(从 main 切出)。

---

### Task 1: 模型与迁移(Alert / QualityRecord / SystemSetting / Workflow 告警三列)

**Files:**
- Modify: `backend/models.py`(追加 3 模型 + Workflow 三列)
- Modify: `backend/app.py`(ensure_column 三条)
- Create: `tests/test_alert_models.py`

- [ ] **Step 1: 写失败测试**

`tests/test_alert_models.py`:

```python
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
    Session = _session(tmp_path)
    with Session() as db:
        db.add(QualityRecord(feature_group_id=1, run_id=None, rows=100,
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
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_alert_models.py -v` → FAIL(ImportError)

- [ ] **Step 3: 写实现**

`backend/models.py`:Workflow 类中 `last_scheduled_at` 之后追加三列:

```python
    alert_on_failure: Mapped[bool] = mapped_column(Boolean, default=True)
    alert_on_success: Mapped[bool] = mapped_column(Boolean, default=False)
    sla_time: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "HH:MM" 工作流时区
```

文件末尾追加:

```python
class Alert(Base):
    """站内告警。kind: run_failed/run_success/sla_miss/quality_drop/materialize_lag"""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    level: Mapped[str] = mapped_column(String(16), default="warning")  # info/warning/error
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="")
    workflow_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class QualityRecord(Base):
    """特征质量记录:每次成功产出落一条,供环比突变检测与趋势展示。"""

    __tablename__ = "quality_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feature_group_id: Mapped[int] = mapped_column(ForeignKey("feature_groups.id"), nullable=False)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    distinct_keys: Mapped[int | None] = mapped_column(Integer, nullable=True)
    null_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SystemSetting(Base):
    """全局 KV 配置(webhook_url、quality_drop_ratio 等),管理员维护。"""

    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, default="")
```

注意 `Float` 需加入 models.py 顶部 sqlalchemy import。

`backend/app.py` ensure_column 区追加:

```python
    ensure_column(engine, "workflows", "alert_on_failure", "BOOLEAN DEFAULT 1")
    ensure_column(engine, "workflows", "alert_on_success", "BOOLEAN DEFAULT 0")
    ensure_column(engine, "workflows", "sla_time", "VARCHAR(5)")
```

同时 `backend/routers/workflows.py`:`WorkflowIn` 增加三个可选字段并落库(create/update 两处赋值),`_wf_out` 输出三字段;`_validate_meta` 校验 sla_time 格式(`HH:MM`,croniter 不管;用正则 `^([01]\d|2[0-3]):[0-5]\d$`),非法 → 400。

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_alert_models.py tests/ -v` → 新增 4 个 PASS,全量 159 passed(workflows 既有测试不受影响——新字段全部可选有默认)

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/app.py backend/routers/workflows.py tests/test_alert_models.py
git commit -m "feat: 告警/质量/系统配置模型与工作流告警三列"
```

---

### Task 2: notify 服务(飞书 Webhook)与系统配置 API

**Files:**
- Create: `backend/services/notify.py`
- Create: `backend/routers/settings.py`
- Modify: `backend/app.py`(挂载)
- Create: `tests/test_notify_settings.py`

- [ ] **Step 1: 写失败测试**

`tests/test_notify_settings.py`:

```python
from backend.services import notify


def test_send_webhook_posts_card(monkeypatch):
    sent = {}

    def fake_post(url, json=None, timeout=None):
        sent.update(url=url, json=json)

        class R:
            status_code = 200

        return R()

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    notify.send_webhook("https://hook", "标题", "正文内容")
    assert sent["url"] == "https://hook"
    assert "标题" in str(sent["json"])


def test_send_webhook_swallows_errors(monkeypatch):
    def boom(*a, **kw):
        raise OSError("network down")

    monkeypatch.setattr(notify.httpx, "post", boom)
    notify.send_webhook("https://hook", "t", "d")  # 不应抛异常


def test_send_webhook_noop_without_url():
    notify.send_webhook("", "t", "d")  # 空 URL 直接返回


def test_settings_api_admin_only(client, admin_headers):
    r = client.put("/api/settings/webhook_url",
                   json={"value": "https://open.feishu.cn/x"}, headers=admin_headers)
    assert r.status_code == 200
    assert client.get("/api/settings/webhook_url",
                      headers=admin_headers).json()["value"].endswith("/x")
    # 覆盖更新
    client.put("/api/settings/webhook_url", json={"value": "https://b"}, headers=admin_headers)
    assert client.get("/api/settings/webhook_url",
                      headers=admin_headers).json()["value"] == "https://b"
    # 非管理员拒绝
    client.post("/api/users", json={"username": "dev", "password": "dev123456",
                                    "role": "developer"}, headers=admin_headers)
    rr = client.post("/api/auth/login", json={"username": "dev", "password": "dev123456"})
    dev = {"Authorization": f"Bearer {rr.json()['token']}"}
    assert client.get("/api/settings/webhook_url", headers=dev).status_code == 403
    assert client.put("/api/settings/webhook_url", json={"value": "x"},
                      headers=dev).status_code == 403


def test_unknown_setting_key_rejected(client, admin_headers):
    assert client.put("/api/settings/nonsense", json={"value": "1"},
                      headers=admin_headers).status_code == 400
```

- [ ] **Step 2: 运行确认失败** → FAIL(ModuleNotFoundError / 404)

- [ ] **Step 3: 写实现**

`backend/services/notify.py`:

```python
"""Webhook 通知:飞书机器人卡片格式。发送失败只记日志,绝不影响主流程。"""
import traceback

import httpx


def send_webhook(url: str, title: str, text: str) -> None:
    if not url:
        return
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": title},
                       "template": "red" if "失败" in title or "超时" in title else "blue"},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": text}}],
        },
    }
    try:
        httpx.post(url, json=payload, timeout=5)
    except Exception:  # noqa: BLE001  通知失败不影响主流程
        traceback.print_exc()


def get_setting(db, key: str, default: str = "") -> str:
    from sqlalchemy import select

    from ..models import SystemSetting

    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    return row.value if row else default
```

`backend/routers/settings.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_db, require_admin
from ..models import SystemSetting
from ..services.audit import record

router = APIRouter(prefix="/settings", tags=["settings"])

ALLOWED_KEYS = ("webhook_url", "quality_drop_ratio", "materialize_lag_hours")


class SettingIn(BaseModel):
    value: str


@router.get("/{key}")
def get_setting(key: str, db=Depends(get_db), _=Depends(require_admin)):
    if key not in ALLOWED_KEYS:
        raise HTTPException(400, f"未知配置项,可选 {ALLOWED_KEYS}")
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    return {"key": key, "value": row.value if row else ""}


@router.put("/{key}")
def put_setting(key: str, body: SettingIn, db=Depends(get_db), admin=Depends(require_admin)):
    if key not in ALLOWED_KEYS:
        raise HTTPException(400, f"未知配置项,可选 {ALLOWED_KEYS}")
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    if row is None:
        db.add(SystemSetting(key=key, value=body.value))
    else:
        row.value = body.value
    record(db, admin, "update_setting", key)
    db.commit()
    return {"ok": True}
```

`backend/app.py` 挂载(online 下方):

```python
    from .routers import settings as settings_router

    app.include_router(settings_router.router, prefix="/api")
```

- [ ] **Step 4: 运行确认通过** → 新增 5 个 PASS,全量 164 passed
- [ ] **Step 5: Commit**

```bash
git add backend/services/notify.py backend/routers/settings.py backend/app.py tests/test_notify_settings.py
git commit -m "feat: 飞书 Webhook 通知服务与系统配置 API"
```

---

### Task 3: 告警产生——run 完结钩子与质量环比突变

**Files:**
- Create: `backend/services/alerts.py`
- Modify: `backend/services/scheduler.py`(_advance_one 完结处调用)
- Modify: `backend/services/task_runner.py`(_register_production 写质量记录+突变检测)
- Create: `tests/test_alerts_emit.py`

- [ ] **Step 1: 写失败测试**

`tests/test_alerts_emit.py`:

```python
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
    with Session() as db:
        ti = db.scalar(select(TaskInstance).where(TaskInstance.task_key == "t1")) \
            if False else None
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
```

- [ ] **Step 2: 运行确认失败** → FAIL(alerts 模块不存在 / Alert 未产生)

- [ ] **Step 3: 写实现**

`backend/services/alerts.py`:

```python
"""告警产生与分发:落 alerts 表 + Webhook(全局配置)。调用方负责 commit 落库部分。"""
from ..models import Alert


def _send(db, title: str, text: str) -> None:
    """读取全局 webhook 配置并发送(失败已在 notify 内吞掉)。"""
    from .notify import get_setting, send_webhook

    send_webhook(get_setting(db, "webhook_url"), title, text)


def emit(db, *, project_id, level, kind, title, detail="",
         workflow_id=None, run_id=None, webhook=True) -> None:
    db.add(Alert(project_id=project_id, level=level, kind=kind, title=title,
                 detail=detail, workflow_id=workflow_id, run_id=run_id))
    if webhook:
        _send(db, title, detail or title)


def on_run_finished(db, wf, run) -> None:
    """run 完结钩子(advance_runs 完结判定后调用,随外层 commit 一起落库)。"""
    if run.state == "failed" and wf.alert_on_failure:
        emit(db, project_id=wf.project_id, level="error", kind="run_failed",
             title=f"工作流「{wf.name}」运行失败",
             detail=f"run_id={run.id} 区间 {run.data_interval_start:%Y-%m-%d %H:%M} ~ "
                    f"{run.data_interval_end:%Y-%m-%d %H:%M}",
             workflow_id=wf.id, run_id=run.id)
    elif run.state == "success" and wf.alert_on_success:
        emit(db, project_id=wf.project_id, level="info", kind="run_success",
             title=f"工作流「{wf.name}」运行成功",
             detail=f"run_id={run.id}", workflow_id=wf.id, run_id=run.id)
```

`backend/services/scheduler.py` `_advance_one` 完结判定块改为:

```python
        if all(t.state in TERMINAL_STATES for t in tis.values()):
            ok = all(t.state in ("success", "skipped") for t in tis.values())
            run.state = "success" if ok else "failed"
            run.finished_at = now
            from .alerts import on_run_finished

            if wf is not None:
                on_run_finished(db, wf, run)
```

`backend/services/task_runner.py` `_register_production` 改造:在更新 fg 的循环里同时写质量记录与突变检测(替换原循环体):

```python
        for fg in fgs:
            fg.last_produced_at = datetime.utcnow()
            fg.last_produced_rows = rows
            _record_quality(db, fg, rows)
        if fgs:
            db.commit()
```

并新增模块函数:

```python
def _record_quality(db, fg, rows) -> None:
    """写质量记录;与上一条对比,降幅超过阈值(默认 0.5)产生 quality_drop 告警。"""
    from sqlalchemy import select

    from ..models import QualityRecord
    from .alerts import emit
    from .notify import get_setting

    prev = db.scalar(select(QualityRecord)
                     .where(QualityRecord.feature_group_id == fg.id)
                     .order_by(QualityRecord.id.desc()).limit(1))
    db.add(QualityRecord(feature_group_id=fg.id, rows=rows))
    if rows is None or prev is None or not prev.rows:
        return
    try:
        threshold = float(get_setting(db, "quality_drop_ratio", "0.5"))
    except ValueError:
        threshold = 0.5
    if rows < prev.rows * (1 - threshold):
        emit(db, project_id=fg.project_id, level="warning", kind="quality_drop",
             title=f"特征组「{fg.name}」产出行数突降",
             detail=f"本次 {rows} 行,上次 {prev.rows} 行,降幅超过 {threshold:.0%}",
             workflow_id=fg.workflow_id)
```

- [ ] **Step 4: 运行确认通过** → 新增 3 个 PASS,全量 167 passed(注意既有 advance/produce 测试不受影响:默认 alert_on_failure=True 会在失败场景多产生 Alert 行,但既有测试不查 alerts 表;webhook 因无配置 URL 为 noop)
- [ ] **Step 5: Commit**

```bash
git add backend/services/alerts.py backend/services/scheduler.py backend/services/task_runner.py tests/test_alerts_emit.py
git commit -m "feat: 运行完结告警与质量环比突变检测"
```

---

### Task 4: SLA 检查(调度器第四阶段)

**Files:**
- Modify: `backend/services/scheduler.py`(check_sla + tick 接入)
- Create: `tests/test_sla.py`

- [ ] **Step 1: 写失败测试**

`tests/test_sla.py`:

```python
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
```

- [ ] **Step 2: 运行确认失败** → FAIL(AttributeError check_sla)

- [ ] **Step 3: 写实现**

`backend/services/scheduler.py`:`__init__` 增加 `self._last_sla_check: datetime | None = None`;类内追加:

```python
    # ---- ④ SLA 检查(60s 节流;当天去重;判定口径:SLA 时刻后,昨天区间的
    # scheduled run 应已 success——即"今天 HH:MM 前应完成昨日数据加工") ----
    SLA_THROTTLE_SEC = 60

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
                ok = db.scalar(select(WorkflowRun.id).where(
                    WorkflowRun.workflow_id == wf.id,
                    WorkflowRun.run_type == "scheduled",
                    WorkflowRun.state == "success",
                    WorkflowRun.data_interval_end >= day_start).limit(1))
                if ok:
                    continue
                dup = db.scalar(select(Alert.id).where(
                    Alert.kind == "sla_miss", Alert.workflow_id == wf.id,
                    Alert.created_at >= day_start).limit(1))
                if dup:
                    continue
                emit(db, project_id=wf.project_id, level="error", kind="sla_miss",
                     title=f"工作流「{wf.name}」SLA 超时",
                     detail=f"应在 {wf.sla_time} 前完成当日调度,当前仍未成功",
                     workflow_id=wf.id)
            db.commit()
```

`tick()` 末尾追加 `self.check_sla()`。

注意:SLA 去重以 Alert.created_at(naive UTC)与工作流本地 day_start 比较存在时区偏差,部门级场景接受(同日判定误差最多数小时,只影响极端跨时区配置);在代码中加一行注释说明。

- [ ] **Step 4: 运行确认通过** → 新增 5 个 PASS,全量 172 passed
- [ ] **Step 5: Commit**

```bash
git add backend/services/scheduler.py tests/test_sla.py
git commit -m "feat: SLA 超时检查(调度器第四阶段,节流+当日去重)"
```

---

### Task 5: 告警中心与监控大盘 API

**Files:**
- Create: `backend/routers/monitoring.py`
- Modify: `backend/app.py`(挂载)
- Create: `tests/test_monitoring_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_monitoring_api.py`:

```python
from datetime import datetime, timedelta


def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_ws(client, admin_headers):
    client.post("/api/users", json={"username": "bob", "password": "bob123456",
                                    "role": "developer"}, headers=admin_headers)
    h = _login(client, "bob", "bob123456")
    pid = client.post("/api/projects", json={"name": "p1", "description": ""},
                      headers=h).json()["id"]
    return {**h, "X-Project-Id": str(pid)}, pid


def _seed_alert(client, pid, kind="run_failed", read=False):
    from backend.models import Alert

    with client.app.state.sessionmaker() as db:
        db.add(Alert(project_id=pid, level="error", kind=kind, title="t", detail="d",
                     read=read))
        db.commit()


def test_alert_center_list_and_read(client, admin_headers):
    h, pid = _mk_ws(client, admin_headers)
    _seed_alert(client, pid)
    _seed_alert(client, pid, kind="quality_drop")
    lst = client.get("/api/alerts", headers=h).json()
    assert len(lst) == 2 and lst[0]["read"] is False
    aid = lst[0]["id"]
    assert client.post(f"/api/alerts/{aid}/read", headers=h).status_code == 200
    lst2 = client.get("/api/alerts?unread_only=1", headers=h).json()
    assert len(lst2) == 1


def test_alert_project_isolation(client, admin_headers):
    h, pid = _mk_ws(client, admin_headers)
    _seed_alert(client, pid)
    client.post("/api/users", json={"username": "eve", "password": "eve123456",
                                    "role": "developer"}, headers=admin_headers)
    eh = _login(client, "eve", "eve123456")
    pid2 = client.post("/api/projects", json={"name": "p2", "description": ""},
                       headers=eh).json()["id"]
    eh = {**eh, "X-Project-Id": str(pid2)}
    assert client.get("/api/alerts", headers=eh).json() == []


def test_dashboard_counts(client, admin_headers):
    h, pid = _mk_ws(client, admin_headers)
    dag = {"nodes": [{"key": "t1", "type": "duckdb_sql",
                      "params": {"sql": "select 1"}}], "edges": []}
    wid = client.post("/api/workflows", json={
        "name": "wf", "description": "", "dag": dag, "cron": None,
        "timezone": "Asia/Shanghai", "catchup": False, "concurrency_limit": 1,
        "failure_policy": "continue"}, headers=h).json()["id"]
    client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h)
    for _ in range(4):
        client.app.state.scheduler.advance_runs()
        client.app.state.executor.poll()
    d = client.get("/api/monitoring/dashboard", headers=h).json()
    assert d["today"]["success"] == 1
    assert d["today"]["failed"] == 0
    assert d["recent_failures"] == []
    assert d["workflows_total"] == 1
    assert isinstance(d["feature_groups"], list)


def test_dashboard_materialize_lag(client, admin_headers):
    h, pid = _mk_ws(client, admin_headers)
    fg = client.post("/api/feature-groups", json={
        "name": "g", "description": "", "entity_keys": ["k"], "event_time_col": "dt",
        "ttl_days": None, "online_enabled": True, "offline_kind": "parquet",
        "offline_location": "g",
        "features": [{"name": "v", "dtype": "int", "description": ""}],
        "upstream_tables": []}, headers=h).json()
    from backend.models import FeatureGroup

    with client.app.state.sessionmaker() as db:
        row = db.get(FeatureGroup, fg["id"])
        row.materialize_watermark = datetime.utcnow() - timedelta(hours=72)
        db.commit()
    d = client.get("/api/monitoring/dashboard", headers=h).json()
    lag = [x for x in d["feature_groups"] if x["id"] == fg["id"]][0]
    assert lag["lag_hours"] >= 71
    # 滞后超过默认阈值(24h)→ 产生 materialize_lag 告警(当天去重)
    alerts = client.get("/api/alerts", headers=h).json()
    assert any(a["kind"] == "materialize_lag" for a in alerts)
    client.get("/api/monitoring/dashboard", headers=h)  # 再次访问不重复告警
    alerts2 = client.get("/api/alerts", headers=h).json()
    assert len([a for a in alerts2 if a["kind"] == "materialize_lag"]) == 1
```

- [ ] **Step 2: 运行确认失败** → FAIL,404

- [ ] **Step 3: 写实现**

`backend/routers/monitoring.py`:

```python
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from ..deps import get_db, get_project_id
from ..models import (
    Alert, FeatureGroup, TaskInstance, Workflow, WorkflowRun,
)
from ..services.alerts import emit
from ..services.notify import get_setting

router = APIRouter(tags=["monitoring"])


@router.get("/alerts")
def list_alerts(unread_only: int = 0, db=Depends(get_db), pid=Depends(get_project_id)):
    q = select(Alert).where(Alert.project_id == pid).order_by(Alert.id.desc()).limit(200)
    if unread_only:
        q = q.where(Alert.read.is_(False))
    return [{"id": a.id, "level": a.level, "kind": a.kind, "title": a.title,
             "detail": a.detail, "workflow_id": a.workflow_id, "run_id": a.run_id,
             "read": a.read, "created_at": a.created_at.isoformat()}
            for a in db.scalars(q).all()]


@router.post("/alerts/{aid}/read")
def mark_read(aid: int, db=Depends(get_db), pid=Depends(get_project_id)):
    a = db.get(Alert, aid)
    if a is None or a.project_id != pid:
        raise HTTPException(404, "告警不存在")
    a.read = True
    db.commit()
    return {"ok": True}


@router.get("/monitoring/dashboard")
def dashboard(db=Depends(get_db), pid=Depends(get_project_id)):
    wf_ids = [w.id for w in db.scalars(
        select(Workflow).where(Workflow.project_id == pid)).all()]
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    def _count(state):
        if not wf_ids:
            return 0
        return db.scalar(select(func.count(WorkflowRun.id)).where(
            WorkflowRun.workflow_id.in_(wf_ids),
            WorkflowRun.created_at >= today,
            WorkflowRun.state == state)) or 0

    failures = []
    if wf_ids:
        rows = db.scalars(select(WorkflowRun).where(
            WorkflowRun.workflow_id.in_(wf_ids), WorkflowRun.state == "failed")
            .order_by(WorkflowRun.id.desc()).limit(10)).all()
        failures = [{"run_id": r.id, "workflow_id": r.workflow_id,
                     "interval": r.data_interval_start.isoformat(),
                     "finished_at": r.finished_at.isoformat() if r.finished_at else None}
                    for r in rows]

    fgs = db.scalars(select(FeatureGroup).where(FeatureGroup.project_id == pid)).all()
    now = datetime.utcnow()
    try:
        lag_threshold = float(get_setting(db, "materialize_lag_hours", "24"))
    except ValueError:
        lag_threshold = 24.0
    fg_out = []
    for fg in fgs:
        lag_hours = None
        if fg.online_enabled and fg.materialize_watermark is not None:
            lag_hours = round((now - fg.materialize_watermark).total_seconds() / 3600, 1)
            if lag_hours > lag_threshold:
                dup = db.scalar(select(Alert.id).where(
                    Alert.kind == "materialize_lag", Alert.project_id == pid,
                    Alert.detail.like(f"fg_id={fg.id};%"),
                    Alert.created_at >= today).limit(1))
                if dup is None:
                    emit(db, project_id=pid, level="warning", kind="materialize_lag",
                         title=f"特征组「{fg.name}」在线物化滞后",
                         detail=f"fg_id={fg.id};水位落后 {lag_hours} 小时(阈值 {lag_threshold})",
                         workflow_id=fg.workflow_id, webhook=False)
        fg_out.append({"id": fg.id, "name": fg.name, "version": fg.version,
                       "online_enabled": fg.online_enabled,
                       "last_produced_at": (fg.last_produced_at.isoformat()
                                            if fg.last_produced_at else None),
                       "lag_hours": lag_hours})
    db.commit()  # 落滞后告警
    return {"today": {"success": _count("success"), "failed": _count("failed"),
                      "running": _count("running")},
            "recent_failures": failures,
            "workflows_total": len(wf_ids),
            "feature_groups": fg_out}
```

`backend/app.py` 挂载(settings 下方):

```python
    from .routers import monitoring as monitoring_router

    app.include_router(monitoring_router.router, prefix="/api")
```

- [ ] **Step 4: 运行确认通过** → 新增 5 个 PASS,全量 177 passed
- [ ] **Step 5: Commit**

```bash
git add backend/routers/monitoring.py backend/app.py tests/test_monitoring_api.py
git commit -m "feat: 告警中心与监控大盘 API(物化滞后惰性扫描)"
```

---

### Task 6: 加固三项(审计同事务 / 孙进程超时 / 在线批量查询)

**Files:**
- Modify: `backend/routers/runs.py`(record 移到 create_run 之前)
- Modify: `backend/services/task_runner.py`(ctx 注入 _timeout_sec)
- Modify: `backend/services/plugins/python_script.py`(subprocess timeout)
- Modify: `backend/services/online_store.py`(query_batch)
- Modify: `backend/routers/online.py`(改用 query_batch)
- Create: `tests/test_hardening.py`

- [ ] **Step 1: 写失败测试**

`tests/test_hardening.py`:

```python
from datetime import datetime

from backend.config import Settings
from backend.services.online_store import ensure_schema, query_batch, upsert
from backend.services.plugins import get_plugin
from backend.services.templating import build_context


def test_query_batch(tmp_path):
    db = tmp_path / "o.db"
    ensure_schema(db)
    upsert(db, 1, [{"k": "a", "v": 1}, {"k": "b", "v": 2}], ["k"], None)
    got = query_batch(db, 1, ["a", "b", "ghost"])
    assert got["a"]["payload"]["v"] == 1
    assert got["b"]["payload"]["v"] == 2
    assert "ghost" not in got


def test_python_script_grandchild_timeout(tmp_path):
    import pytest

    s = Settings(storage_dir=str(tmp_path))
    s.ensure_dirs()
    (s.scripts_dir / "loop.py").write_text("import time\nwhile True: time.sleep(1)\n",
                                           encoding="utf-8")
    ctx = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))
    ctx["_timeout_sec"] = 2
    fn = get_plugin("python_script")
    with pytest.raises(Exception):  # TimeoutExpired 或 RuntimeError
        fn({"script": "loop.py"}, ctx, s)


def test_trigger_audit_atomic(client, admin_headers):
    """审计与 run 创建同事务:trigger 后 audit 与 run 必然同时存在。"""
    from sqlalchemy import select

    from backend.models import AuditLog, WorkflowRun

    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    client.post("/api/users", json={"username": "bob", "password": "bob123456",
                                    "role": "developer"}, headers=admin_headers)
    r = client.post("/api/auth/login", json={"username": "bob", "password": "bob123456"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    pid = client.post("/api/projects", json={"name": "p1", "description": ""},
                      headers=h).json()["id"]
    h = {**h, "X-Project-Id": str(pid)}
    dag = {"nodes": [{"key": "t1", "type": "duckdb_sql",
                      "params": {"sql": "select 1"}}], "edges": []}
    wid = client.post("/api/workflows", json={
        "name": "wf", "description": "", "dag": dag, "cron": None,
        "timezone": "Asia/Shanghai", "catchup": False, "concurrency_limit": 1,
        "failure_policy": "continue"}, headers=h).json()["id"]
    client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h)
    with client.app.state.sessionmaker() as db:
        assert db.scalar(select(WorkflowRun)) is not None
        assert db.scalar(select(AuditLog).where(
            AuditLog.action == "trigger_run")) is not None
```

- [ ] **Step 2: 运行确认失败** → FAIL(query_batch 不存在等)

- [ ] **Step 3: 写实现**

1. `backend/online_store.py` 追加:

```python
def query_batch(path, fg_id: int, entity_keys: list[str]) -> dict[str, dict]:
    """批量点查:一次连接一次 IN 查询。返回 {entity_key: {payload, event_time, updated_at}}。"""
    if not entity_keys:
        return {}
    ensure_schema(path)
    con = _connect(path)
    try:
        marks = ",".join("?" * len(entity_keys))
        rows = con.execute(
            f"SELECT entity_key, payload, event_time, updated_at FROM online_features "
            f"WHERE feature_group_id=? AND entity_key IN ({marks})",
            (fg_id, *entity_keys)).fetchall()
    finally:
        con.close()
    return {r[0]: {"payload": json.loads(r[1]), "event_time": r[2], "updated_at": r[3]}
            for r in rows}
```

2. `backend/routers/online.py` `_query_fg` 改为批量:先对全部 keys 计算 entity_key(收集 ek 列表与映射),调用 `query_batch` 一次,再循环组装 results(逻辑同前:miss / TTL 判定)。

3. `backend/services/task_runner.py`:构造 ctx 后追加 `ctx["_timeout_sec"] = timeout_sec`(timeout_sec 已在首会话捕获;为 None 则不加)。

4. `backend/services/plugins/python_script.py`:`subprocess.run(..., timeout=ctx.get("_timeout_sec"))`(None = 不限);捕获 `subprocess.TimeoutExpired` 重抛 `RuntimeError(f"脚本执行超时({...}s)")`。

5. `backend/routers/runs.py`:`trigger_run` 与 `backfill` 中,把 `record(db, user, ...)` 移到对应 `create_run(...)` 调用之前(record 仅 db.add,create_run 内部 commit 将审计一并提交,实现同事务);函数末尾原有的 `db.commit()` 保留(幂等空提交无害)。backfill 中 record 在循环前置(detail 中 created 数量改为区间描述,去掉 x{created} 计数,或先数区间再 record——实现取后者:先用 croniter 数出区间数再 record 再循环创建)。简单做法:循环结束后再 record+commit 仍是分离事务——不满足;改为:先推演区间列表(croniter 循环收集 pairs),`record(db, user, "backfill", f"{start}~{end} x{len(pairs)}")`,然后逐区间 create_run(第一个 create_run 的 commit 带上审计)。若 pairs 为空,record 后直接 db.commit()。

- [ ] **Step 4: 运行确认通过** → 新增 3 个 PASS,全量 180 passed(在线 API 既有 5 测试验证批量改造无回归)
- [ ] **Step 5: Commit**

```bash
git add backend/services/online_store.py backend/routers/online.py backend/services/task_runner.py backend/services/plugins/python_script.py backend/routers/runs.py tests/test_hardening.py
git commit -m "feat: 加固——审计同事务/孙进程超时/在线批量查询"
```

---

### Task 7: 全量回归与收尾

- [ ] **Step 1:** `D:/conda/envs/scpy310/python.exe -m pytest tests/ -v` → 全部 PASS(约 180)
- [ ] **Step 2:** `D:/conda/envs/scpy310/python.exe -c "from backend.app import create_app; app = create_app(); print('routes:', len(app.routes))"` → 正常(ensure_column 对既有库生效)
- [ ] **Step 3:** `git status --short` → 空

---

## Self-Review 记录

- **Spec 覆盖**:spec §8 监控告警(大盘/质量环比/物化滞后/SLA/Webhook 飞书/告警中心/三类策略绑定)全部落地;遗留加固项三件全部消化。SLA 判定口径(今天 HH:MM 前完成"昨日区间"调度)在代码注释中显式化。
- **占位符**:无;Task 6 backfill 审计同事务的实现路径已写明(先推演区间→record→逐区间 create_run)。
- **类型一致性**:`emit(webhook=False)` 用于滞后告警(大盘扫描高频,避免重复推送);`alerts._send` 测试通过 monkeypatch 替身;`ctx["_timeout_sec"]` 下划线前缀避开模板变量语义(render 仅替换 `{{ }}` 出现的变量,不受影响);`query_batch` 返回 dict 以 entity_key 为键,与 `_query_fg` 组装逻辑对应。
