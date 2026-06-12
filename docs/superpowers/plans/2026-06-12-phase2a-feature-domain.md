# Phase 2a:特征域与下推/依赖插件 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 特征管理核心落地:FeatureGroup/Feature/血缘模型、特征组 CRUD(schema 变更升版本 v1/v2 并存)、生产即注册(任务成功回写产出信息)、`sql_pushdown` 与 `dependent` 插件。

**Architecture:** 特征组绑定产出任务 (workflow_id, task_key);task_runner 成功终态后回写 last_produced_at/rows——"特征不脱离调度孤立存在"。插件需访问元数据库的(sql_pushdown 查连接、dependent 查实例)通过 `env.db_path` 自行开 engine(env=Settings)。血缘为字符串节点边表(`table:dw.x` → `feature_group:3`)。在线物化/查询归 Phase 2b。

**约定:** 命令在 `D:\feature-platform`,Python `D:/conda/envs/scpy310/python.exe`。分支 `feature/phase2a-feature-domain`(从 main 切出)。

---

### Task 1: 特征域三模型

**Files:**
- Modify: `backend/models.py`(追加 FeatureGroup/Feature/LineageEdge)
- Create: `tests/test_feature_models.py`

- [ ] **Step 1: 写失败测试**

`tests/test_feature_models.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.db import Base, make_engine
from backend.models import Feature, FeatureGroup, LineageEdge


def _session(tmp_path):
    engine = make_engine(tmp_path / "meta.db")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_feature_group_defaults_and_children(tmp_path):
    Session = _session(tmp_path)
    with Session() as db:
        fg = FeatureGroup(project_id=None, name="cust_daily", entity_keys_json='["cust_no"]',
                          offline_kind="parquet", offline_location="cust_daily")
        db.add(fg)
        db.flush()
        db.add(Feature(feature_group_id=fg.id, name="amt_30d", dtype="double",
                       description="近30天交易额"))
        db.add(LineageEdge(project_id=None, src="table:dw.cust_base",
                           dst=f"feature_group:{fg.id}"))
        db.commit()
        got = db.query(FeatureGroup).one()
        assert got.version == 1
        assert got.online_enabled is False
        assert got.last_produced_at is None
        assert got.materialize_watermark is None
        assert db.query(Feature).one().description == "近30天交易额"
        assert db.query(LineageEdge).one().src == "table:dw.cust_base"


def test_feature_group_unique_per_project_name_version(tmp_path):
    Session = _session(tmp_path)
    with Session() as db:
        db.add(FeatureGroup(project_id=1, name="g", version=1, entity_keys_json='["k"]',
                            offline_kind="parquet", offline_location="g"))
        db.commit()
        db.add(FeatureGroup(project_id=1, name="g", version=1, entity_keys_json='["k"]',
                            offline_kind="parquet", offline_location="g"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_feature_unique_per_group(tmp_path):
    Session = _session(tmp_path)
    with Session() as db:
        fg = FeatureGroup(project_id=None, name="g", entity_keys_json='["k"]',
                          offline_kind="parquet", offline_location="g")
        db.add(fg)
        db.flush()
        db.add(Feature(feature_group_id=fg.id, name="f1", dtype="int"))
        db.commit()
        db.add(Feature(feature_group_id=fg.id, name="f1", dtype="int"))
        with pytest.raises(IntegrityError):
            db.commit()
```

注:测试若违反 FK(project_id=1 无项目行),沿用项目惯例改为 `project_id=None` 或先建真实项目——本计划测试 `test_feature_group_unique_per_project_name_version` 用 `project_id=1` 需要先插入 Project 行;实现时按既有测试文件的做法(见 tests/test_scheduling_models.py 的 `test_workflow_name_unique_per_project`)插入真实 User/Project 后再用其 id。

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_feature_models.py -v`
Expected: FAIL,`ImportError`

- [ ] **Step 3: 写实现**

`backend/models.py` 末尾追加:

```python
class FeatureGroup(Base):
    """特征组:特征管理核心单元。绑定产出任务(workflow_id+task_key),生产即注册。
    schema(特征清单)变更升版本:同 (project,name) 下新行 version+1,旧版本并存。"""

    __tablename__ = "feature_groups"
    __table_args__ = (UniqueConstraint("project_id", "name", "version", name="uq_fg"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str] = mapped_column(Text, default="")
    entity_keys_json: Mapped[str] = mapped_column(Text, nullable=False)  # 主键列名 JSON 数组
    event_time_col: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ttl_days: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 在线 TTL
    online_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    offline_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # parquet/warehouse
    offline_location: Mapped[str] = mapped_column(String(255), nullable=False)  # 目录名或库表名
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    workflow_id: Mapped[int | None] = mapped_column(ForeignKey("workflows.id"), nullable=True)
    task_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_produced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_produced_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    materialize_watermark: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Feature(Base):
    __tablename__ = "features"
    __table_args__ = (UniqueConstraint("feature_group_id", "name", name="uq_feature"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feature_group_id: Mapped[int] = mapped_column(ForeignKey("feature_groups.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    dtype: Mapped[str] = mapped_column(String(32), default="double")
    description: Mapped[str] = mapped_column(Text, default="")  # 业务口径,审计留痕


class LineageEdge(Base):
    """血缘边:节点用 '类型:标识' 字符串(table:dw.x / feature_group:3 / workflow:5)。"""

    __tablename__ = "lineage_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    src: Mapped[str] = mapped_column(String(255), nullable=False)
    dst: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_feature_models.py tests/ -v`
Expected: 新增 3 个 PASS,全量 116 passed

- [ ] **Step 5: Commit**

```bash
git add backend/models.py tests/test_feature_models.py
git commit -m "feat: 特征域模型(FeatureGroup/Feature/血缘边表)"
```

---

### Task 2: 特征组 CRUD API(版本语义 + 血缘登记)

**Files:**
- Create: `backend/routers/feature_groups.py`
- Modify: `backend/app.py`(挂载路由)
- Create: `tests/test_feature_groups_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_feature_groups_api.py`:

```python
def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_ws(client, admin_headers, name="bob", project="p1"):
    client.post("/api/users", json={"username": name, "password": name + "123456",
                                    "role": "developer"}, headers=admin_headers)
    h = _login(client, name, name + "123456")
    pid = client.post("/api/projects", json={"name": project, "description": ""},
                      headers=h).json()["id"]
    return {**h, "X-Project-Id": str(pid)}, pid


def _mk_wf(client, h, name="wf"):
    dag = {"nodes": [{"key": "t1", "type": "duckdb_sql",
                      "params": {"sql": "select 1"}}], "edges": []}
    return client.post("/api/workflows", json={
        "name": name, "description": "", "dag": dag, "cron": None,
        "timezone": "Asia/Shanghai", "catchup": False, "concurrency_limit": 1,
        "failure_policy": "continue"}, headers=h).json()["id"]


FG = {"name": "cust_daily", "description": "客户日特征", "entity_keys": ["cust_no"],
      "event_time_col": "etl_date", "ttl_days": 7, "online_enabled": False,
      "offline_kind": "parquet", "offline_location": "cust_daily",
      "features": [{"name": "amt_30d", "dtype": "double", "description": "近30天交易额"}],
      "upstream_tables": ["table:dw.cust_base"]}


def test_create_and_detail(client, admin_headers):
    h, pid = _mk_ws(client, admin_headers)
    wid = _mk_wf(client, h)
    r = client.post("/api/feature-groups", json={**FG, "workflow_id": wid, "task_key": "t1"},
                    headers=h)
    assert r.status_code == 200
    fid = r.json()["id"]
    assert r.json()["version"] == 1
    d = client.get(f"/api/feature-groups/{fid}", headers=h).json()
    assert d["entity_keys"] == ["cust_no"]
    assert d["features"][0]["name"] == "amt_30d"
    assert d["upstream_tables"] == ["table:dw.cust_base"]
    assert d["workflow_id"] == wid and d["task_key"] == "t1"


def test_validation_errors(client, admin_headers):
    h, pid = _mk_ws(client, admin_headers)
    assert client.post("/api/feature-groups", json={**FG, "entity_keys": []},
                       headers=h).status_code == 400
    assert client.post("/api/feature-groups", json={**FG, "offline_kind": "csv"},
                       headers=h).status_code == 400
    dup = [{"name": "a", "dtype": "int", "description": ""}] * 2
    assert client.post("/api/feature-groups", json={**FG, "features": dup},
                       headers=h).status_code == 400


def test_bind_requires_valid_task(client, admin_headers):
    h, pid = _mk_ws(client, admin_headers)
    wid = _mk_wf(client, h)
    r = client.post("/api/feature-groups",
                    json={**FG, "workflow_id": wid, "task_key": "ghost"}, headers=h)
    assert r.status_code == 400
    r = client.post("/api/feature-groups",
                    json={**FG, "workflow_id": 9999, "task_key": "t1"}, headers=h)
    assert r.status_code == 400


def test_update_schema_bumps_version(client, admin_headers):
    h, pid = _mk_ws(client, admin_headers)
    fid = client.post("/api/feature-groups", json=FG, headers=h).json()["id"]
    # 仅改描述:不升版本
    r = client.put(f"/api/feature-groups/{fid}", json={**FG, "description": "x"}, headers=h)
    assert r.json()["version"] == 1 and r.json()["id"] == fid
    # 特征清单变化:升版本,新行
    feats2 = FG["features"] + [{"name": "cnt_7d", "dtype": "int", "description": "近7天笔数"}]
    r = client.put(f"/api/feature-groups/{fid}", json={**FG, "features": feats2}, headers=h)
    assert r.json()["version"] == 2 and r.json()["id"] != fid
    # 默认列表只显示最新版本;all_versions=1 显示全部
    lst = client.get("/api/feature-groups", headers=h).json()
    assert len(lst) == 1 and lst[0]["version"] == 2
    lst_all = client.get("/api/feature-groups?all_versions=1", headers=h).json()
    assert {x["version"] for x in lst_all} == {1, 2}


def test_duplicate_name_rejected(client, admin_headers):
    h, pid = _mk_ws(client, admin_headers)
    client.post("/api/feature-groups", json=FG, headers=h)
    assert client.post("/api/feature-groups", json=FG, headers=h).status_code == 400


def test_project_isolation(client, admin_headers):
    h1, _ = _mk_ws(client, admin_headers, name="bob", project="p1")
    h2, _ = _mk_ws(client, admin_headers, name="eve", project="p2")
    fid = client.post("/api/feature-groups", json=FG, headers=h1).json()["id"]
    assert client.get("/api/feature-groups", headers=h2).json() == []
    assert client.get(f"/api/feature-groups/{fid}", headers=h2).status_code == 404


def test_lineage_endpoint(client, admin_headers):
    h, pid = _mk_ws(client, admin_headers)
    fid = client.post("/api/feature-groups", json=FG, headers=h).json()["id"]
    edges = client.get("/api/lineage", headers=h).json()
    assert {"src": "table:dw.cust_base", "dst": f"feature_group:{fid}"} in [
        {"src": e["src"], "dst": e["dst"]} for e in edges]
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_feature_groups_api.py -v`
Expected: FAIL,404

- [ ] **Step 3: 写实现**

`backend/routers/feature_groups.py`:

```python
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_current_user, get_db, get_project_id
from ..models import Feature, FeatureGroup, LineageEdge, Workflow, WorkflowVersion
from ..services.audit import record

router = APIRouter(tags=["feature-groups"])

OFFLINE_KINDS = ("parquet", "warehouse")


class FeatureIn(BaseModel):
    name: str
    dtype: str = "double"
    description: str = ""


class FeatureGroupIn(BaseModel):
    name: str
    description: str = ""
    entity_keys: list[str]
    event_time_col: str | None = None
    ttl_days: int | None = None
    online_enabled: bool = False
    offline_kind: str = "parquet"
    offline_location: str
    workflow_id: int | None = None
    task_key: str | None = None
    features: list[FeatureIn]
    upstream_tables: list[str] = []


def _validate(db, body: FeatureGroupIn, pid: int) -> None:
    if not body.entity_keys:
        raise HTTPException(400, "至少一个主键列")
    if body.offline_kind not in OFFLINE_KINDS:
        raise HTTPException(400, f"离线落地须为 {OFFLINE_KINDS}")
    names = [f.name for f in body.features]
    if len(set(names)) != len(names):
        raise HTTPException(400, "特征名重复")
    if body.ttl_days is not None and body.ttl_days < 1:
        raise HTTPException(400, "TTL 须 ≥1 天")
    if (body.workflow_id is None) != (body.task_key is None):
        raise HTTPException(400, "workflow_id 与 task_key 须同时提供")
    if body.workflow_id is not None:
        wf = db.get(Workflow, body.workflow_id)
        if wf is None or wf.project_id != pid:
            raise HTTPException(400, "绑定的工作流不存在")
        ver = db.get(WorkflowVersion, wf.current_version_id)
        keys = [n["key"] for n in json.loads(ver.dag_json)["nodes"]] if ver else []
        if body.task_key not in keys:
            raise HTTPException(400, f"工作流中不存在节点 {body.task_key}")


def _fg_out(db, fg: FeatureGroup, with_children: bool = False) -> dict:
    out = {"id": fg.id, "name": fg.name, "version": fg.version,
           "description": fg.description, "entity_keys": json.loads(fg.entity_keys_json),
           "event_time_col": fg.event_time_col, "ttl_days": fg.ttl_days,
           "online_enabled": fg.online_enabled, "offline_kind": fg.offline_kind,
           "offline_location": fg.offline_location, "workflow_id": fg.workflow_id,
           "task_key": fg.task_key,
           "last_produced_at": fg.last_produced_at.isoformat() if fg.last_produced_at else None,
           "last_produced_rows": fg.last_produced_rows,
           "materialize_watermark": (fg.materialize_watermark.isoformat()
                                     if fg.materialize_watermark else None),
           "created_at": fg.created_at.isoformat()}
    if with_children:
        feats = db.scalars(select(Feature).where(Feature.feature_group_id == fg.id)
                           .order_by(Feature.id)).all()
        out["features"] = [{"name": f.name, "dtype": f.dtype, "description": f.description}
                           for f in feats]
        ups = db.scalars(select(LineageEdge).where(
            LineageEdge.dst == f"feature_group:{fg.id}")).all()
        out["upstream_tables"] = [e.src for e in ups]
    return out


def _get_in_project(db, fid: int, pid: int) -> FeatureGroup:
    fg = db.get(FeatureGroup, fid)
    if fg is None or fg.project_id != pid:
        raise HTTPException(404, "特征组不存在")
    return fg


def _insert_children(db, fg: FeatureGroup, body: FeatureGroupIn, pid: int) -> None:
    for f in body.features:
        db.add(Feature(feature_group_id=fg.id, name=f.name, dtype=f.dtype,
                       description=f.description))
    for src in body.upstream_tables:
        db.add(LineageEdge(project_id=pid, src=src, dst=f"feature_group:{fg.id}"))


@router.get("/feature-groups")
def list_feature_groups(all_versions: int = 0, db=Depends(get_db), pid=Depends(get_project_id)):
    rows = db.scalars(select(FeatureGroup).where(FeatureGroup.project_id == pid)
                      .order_by(FeatureGroup.name, FeatureGroup.version.desc())).all()
    if not all_versions:
        seen: set[str] = set()
        rows = [r for r in rows if not (r.name in seen or seen.add(r.name))]
    return [_fg_out(db, r) for r in rows]


@router.post("/feature-groups")
def create_feature_group(body: FeatureGroupIn, db=Depends(get_db),
                         user=Depends(get_current_user), pid=Depends(get_project_id)):
    _validate(db, body, pid)
    if db.scalar(select(FeatureGroup).where(FeatureGroup.project_id == pid,
                                            FeatureGroup.name == body.name).limit(1)):
        raise HTTPException(400, "特征组名已存在(更新请用 PUT)")
    fg = FeatureGroup(project_id=pid, name=body.name, description=body.description,
                      entity_keys_json=json.dumps(body.entity_keys, ensure_ascii=False),
                      event_time_col=body.event_time_col, ttl_days=body.ttl_days,
                      online_enabled=body.online_enabled, offline_kind=body.offline_kind,
                      offline_location=body.offline_location, owner_id=user.id,
                      workflow_id=body.workflow_id, task_key=body.task_key)
    db.add(fg)
    db.flush()
    _insert_children(db, fg, body, pid)
    record(db, user, "create_feature_group", body.name, project_id=pid)
    db.commit()
    return _fg_out(db, fg)


@router.get("/feature-groups/{fid}")
def get_feature_group(fid: int, db=Depends(get_db), pid=Depends(get_project_id)):
    return _fg_out(db, _get_in_project(db, fid, pid), with_children=True)


@router.put("/feature-groups/{fid}")
def update_feature_group(fid: int, body: FeatureGroupIn, db=Depends(get_db),
                         user=Depends(get_current_user), pid=Depends(get_project_id)):
    fg = _get_in_project(db, fid, pid)
    _validate(db, body, pid)
    old = db.scalars(select(Feature).where(Feature.feature_group_id == fid)).all()
    old_schema = {(f.name, f.dtype) for f in old}
    new_schema = {(f.name, f.dtype) for f in body.features}
    if old_schema == new_schema:
        # 元数据更新,不升版本(描述/TTL/绑定/血缘可改)
        fg.description = body.description
        fg.event_time_col = body.event_time_col
        fg.ttl_days = body.ttl_days
        fg.online_enabled = body.online_enabled
        fg.offline_kind, fg.offline_location = body.offline_kind, body.offline_location
        fg.workflow_id, fg.task_key = body.workflow_id, body.task_key
        for f in old:  # 口径描述同步
            for nf in body.features:
                if nf.name == f.name:
                    f.description = nf.description
        db.query(LineageEdge).filter(LineageEdge.dst == f"feature_group:{fid}").delete()
        for src in body.upstream_tables:
            db.add(LineageEdge(project_id=pid, src=src, dst=f"feature_group:{fid}"))
        record(db, user, "update_feature_group", fg.name, project_id=pid)
        db.commit()
        return _fg_out(db, fg)
    # schema 变化 → 新版本行,旧版本并存
    max_ver = db.scalar(select(FeatureGroup.version)
                        .where(FeatureGroup.project_id == pid, FeatureGroup.name == fg.name)
                        .order_by(FeatureGroup.version.desc()).limit(1)) or 1
    new_fg = FeatureGroup(project_id=pid, name=fg.name, version=max_ver + 1,
                          description=body.description,
                          entity_keys_json=json.dumps(body.entity_keys, ensure_ascii=False),
                          event_time_col=body.event_time_col, ttl_days=body.ttl_days,
                          online_enabled=body.online_enabled, offline_kind=body.offline_kind,
                          offline_location=body.offline_location, owner_id=user.id,
                          workflow_id=body.workflow_id, task_key=body.task_key)
    db.add(new_fg)
    db.flush()
    _insert_children(db, new_fg, body, pid)
    record(db, user, "upgrade_feature_group", f"{fg.name} v{max_ver + 1}", project_id=pid)
    db.commit()
    return _fg_out(db, new_fg)


@router.get("/lineage")
def list_lineage(db=Depends(get_db), pid=Depends(get_project_id)):
    rows = db.scalars(select(LineageEdge).where(LineageEdge.project_id == pid)
                      .order_by(LineageEdge.id)).all()
    return [{"id": e.id, "src": e.src, "dst": e.dst} for e in rows]
```

`backend/app.py` 挂载(runs 路由下方):

```python
    from .routers import feature_groups as feature_groups_router

    app.include_router(feature_groups_router.router, prefix="/api")
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_feature_groups_api.py tests/ -v`
Expected: 新增 7 个 PASS,全量 123 passed

- [ ] **Step 5: Commit**

```bash
git add backend/routers/feature_groups.py backend/app.py tests/test_feature_groups_api.py
git commit -m "feat: 特征组 CRUD(版本语义/血缘登记/绑定校验/项目隔离)"
```

---

### Task 3: 生产即注册(task_runner 成功回写)

**Files:**
- Modify: `backend/services/task_runner.py`
- Create: `tests/test_produce_register.py`

- [ ] **Step 1: 写失败测试**

`tests/test_produce_register.py`:

```python
import json
from datetime import datetime

from sqlalchemy import select

from backend.models import FeatureGroup, TaskInstance
from backend.services.scheduler import Scheduler
from backend.services.task_runner import run_task
from tests.test_scheduler_advance import _mk_run
from tests.test_scheduler_create import make_env, utc


def test_success_updates_bound_feature_group(tmp_path):
    Session, wf_id = make_env(tmp_path)
    with Session() as db:
        db.add(FeatureGroup(project_id=None, name="g", entity_keys_json='["k"]',
                            offline_kind="parquet", offline_location="g",
                            workflow_id=wf_id, task_key="t1"))
        db.commit()
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    with Session() as db:
        ti = db.scalar(select(TaskInstance).where(
            TaskInstance.run_id == rid, TaskInstance.task_key == "t1"))
        ti.state = "running"
        ti.try_number = 1
        ti.started_at = ti.heartbeat_at = datetime(2026, 6, 12)
        db.commit()
        tid = ti.id
    run_task(str(tmp_path / "meta.db"), tid, str(tmp_path))
    with Session() as db:
        fg = db.scalar(select(FeatureGroup))
    assert fg.last_produced_at is not None
    assert fg.last_produced_rows == 1  # duckdb select 1


def test_failure_does_not_update(tmp_path):
    Session, wf_id = make_env(tmp_path)
    with Session() as db:
        db.add(FeatureGroup(project_id=None, name="g", entity_keys_json='["k"]',
                            offline_kind="parquet", offline_location="g",
                            workflow_id=wf_id, task_key="t1"))
        db.commit()
    sched = Scheduler(Session, now_fn=lambda: utc(2026, 6, 12))
    rid = _mk_run(Session, sched, wf_id)
    with Session() as db:
        ti = db.scalar(select(TaskInstance).where(
            TaskInstance.run_id == rid, TaskInstance.task_key == "t1"))
        ti.params_json = json.dumps({"sql": "select * from ghost"})
        ti.state = "running"
        ti.try_number = 3  # 直接耗尽 → failed
        ti.started_at = ti.heartbeat_at = datetime(2026, 6, 12)
        db.commit()
        tid = ti.id
    run_task(str(tmp_path / "meta.db"), tid, str(tmp_path))
    with Session() as db:
        fg = db.scalar(select(FeatureGroup))
    assert fg.last_produced_at is None
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_produce_register.py -v`
Expected: 第一个测试 FAIL(last_produced_at 为 None)

- [ ] **Step 3: 写实现**

`backend/services/task_runner.py`:
1. 在首个 `with Session() as db:` 块中捕获 `run_workflow_id = run.workflow_id`(与 task_type 等并列保存)。
2. 在终态原子写入(`db.execute(update(...))` + commit)之后追加:

```python
    if state == "success":
        _register_production(Session, run_workflow_id, task_key, result_json)
```

3. 模块内新增函数:

```python
def _register_production(Session, workflow_id: int, task_key: str,
                         result_json: str | None) -> None:
    """生产即注册:绑定 (workflow_id, task_key) 的特征组回写最近产出时间/行数。"""
    from sqlalchemy import select

    from ..models import FeatureGroup

    rows = None
    if result_json:
        try:
            rows = json.loads(result_json).get("rows")
        except (ValueError, AttributeError):
            rows = None
    with Session() as db:
        fgs = db.scalars(select(FeatureGroup).where(
            FeatureGroup.workflow_id == workflow_id,
            FeatureGroup.task_key == task_key)).all()
        for fg in fgs:
            fg.last_produced_at = datetime.utcnow()
            fg.last_produced_rows = rows
        if fgs:
            db.commit()
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_produce_register.py tests/ -v`
Expected: 新增 2 个 PASS,全量 125 passed

- [ ] **Step 5: Commit**

```bash
git add backend/services/task_runner.py tests/test_produce_register.py
git commit -m "feat: 生产即注册——任务成功回写特征组产出信息"
```

---

### Task 4: sql_pushdown 插件

**Files:**
- Create: `backend/services/plugins/sql_pushdown.py`
- Modify: `backend/services/plugins/__init__.py`(注册分支)
- Create: `tests/test_plugin_pushdown.py`

- [ ] **Step 1: 写失败测试**

`tests/test_plugin_pushdown.py`:

```python
import json
from datetime import datetime

import pytest
from sqlalchemy.orm import sessionmaker

from backend.config import Settings
from backend.db import Base, make_engine
from backend.models import Connection
from backend.services.plugins import get_plugin
from backend.services.secrets import encrypt_text
from backend.services.templating import build_context

CTX = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))


def _env_with_conn(tmp_path):
    s = Settings(storage_dir=str(tmp_path))
    s.ensure_dirs()
    engine = make_engine(s.db_path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(Connection(name="dw", conn_type="spark", host="h", port=10000,
                          username="u", password_enc=encrypt_text("pw", s.storage_dir),
                          database="dw"))
        db.commit()
        cid = db.query(Connection).one().id
    engine.dispose()
    return s, cid


def test_pushdown_executes_rendered_statements(tmp_path, monkeypatch):
    env, cid = _env_with_conn(tmp_path)
    calls = {}

    def fake_exec(conn_type, host, port, username, password, database, statements):
        calls.update(locals())

    from backend.services.plugins import sql_pushdown

    monkeypatch.setattr(sql_pushdown, "_exec_statements", fake_exec)
    fn = get_plugin("sql_pushdown")
    result = fn({"connection_id": cid,
                 "sql": "insert overwrite t partition(dt='{{ ds }}') select 1; analyze table t"},
                CTX, env)
    assert calls["conn_type"] == "spark"
    assert calls["password"] == "pw"  # 解密后传递
    assert calls["statements"] == [
        "insert overwrite t partition(dt='2026-06-11') select 1", "analyze table t"]
    assert result["rows"] is None  # 未配置 count_sql


def test_pushdown_count_and_min_guard(tmp_path, monkeypatch):
    env, cid = _env_with_conn(tmp_path)
    from backend.services.plugins import sql_pushdown

    monkeypatch.setattr(sql_pushdown, "_exec_statements", lambda *a: None)
    monkeypatch.setattr(sql_pushdown, "_exec_scalar", lambda *a: 5)
    fn = get_plugin("sql_pushdown")
    result = fn({"connection_id": cid, "sql": "select 1",
                 "count_sql": "select count(*) from t where dt='{{ ds }}'",
                 "expect_rows_min": 1}, CTX, env)
    assert result["rows"] == 5
    with pytest.raises(RuntimeError, match="低于下限"):
        fn({"connection_id": cid, "sql": "select 1",
            "count_sql": "select count(*) from t", "expect_rows_min": 10}, CTX, env)


def test_pushdown_missing_connection(tmp_path):
    env, _ = _env_with_conn(tmp_path)
    fn = get_plugin("sql_pushdown")
    with pytest.raises(ValueError, match="连接不存在"):
        fn({"connection_id": 999, "sql": "select 1"}, CTX, env)
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_plugin_pushdown.py -v`
Expected: FAIL(sql_pushdown 仍是"未实现")

- [ ] **Step 3: 写实现**

`backend/services/plugins/sql_pushdown.py`:

```python
"""sql_pushdown 插件:渲染后的 SQL 下推到 Spark ThriftServer / MySQL 源端执行。
params: {connection_id, sql, count_sql?, expect_rows_min?}
分号分隔多语句逐条执行;count_sql 用于产出行数统计与下限校验(0 行防呆)。"""
from ..templating import render


def _exec_statements(conn_type, host, port, username, password, database, statements):
    if conn_type == "mysql":
        import pymysql

        conn = pymysql.connect(host=host, port=port, user=username, password=password,
                               database=database or None, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                for s in statements:
                    cur.execute(s)
            conn.commit()
        finally:
            conn.close()
    elif conn_type == "spark":
        from pyhive import hive

        conn = hive.connect(host=host, port=port, username=username or None,
                            database=database or "default")
        try:
            cur = conn.cursor()
            for s in statements:
                cur.execute(s)
        finally:
            conn.close()
    else:
        raise ValueError(f"不支持的连接类型: {conn_type}")


def _exec_scalar(conn_type, host, port, username, password, database, sql):
    if conn_type == "mysql":
        import pymysql

        conn = pymysql.connect(host=host, port=port, user=username, password=password,
                               database=database or None, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                return cur.fetchone()[0]
        finally:
            conn.close()
    elif conn_type == "spark":
        from pyhive import hive

        conn = hive.connect(host=host, port=port, username=username or None,
                            database=database or "default")
        try:
            cur = conn.cursor()
            cur.execute(sql)
            return cur.fetchone()[0]
        finally:
            conn.close()
    raise ValueError(f"不支持的连接类型: {conn_type}")


def _connection_info(params: dict, env) -> tuple:
    from sqlalchemy.orm import sessionmaker

    from ...db import make_engine
    from ...models import Connection
    from ..secrets import decrypt_text

    engine = make_engine(env.db_path)
    try:
        Session = sessionmaker(bind=engine)
        with Session() as db:
            c = db.get(Connection, params["connection_id"])
            if c is None:
                raise ValueError("连接不存在")
            password = decrypt_text(c.password_enc, env.storage_dir) if c.password_enc else ""
            return (c.conn_type, c.host, c.port, c.username, password, c.database)
    finally:
        engine.dispose()


def execute(params: dict, ctx: dict, env) -> dict:
    info = _connection_info(params, env)
    sql = render(params["sql"], ctx)
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    _exec_statements(*info, statements)
    rows = None
    if params.get("count_sql"):
        rows = int(_exec_scalar(*info, render(params["count_sql"], ctx)))
        if params.get("expect_rows_min") is not None and rows < int(params["expect_rows_min"]):
            raise RuntimeError(f"产出行数 {rows} 低于下限 {params['expect_rows_min']}")
    return {"rows": rows}
```

`backend/services/plugins/__init__.py` 中 `get_plugin` 增加分支(在 python_script 分支后):

```python
    if task_type == "sql_pushdown":
        from .sql_pushdown import execute

        return execute
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_plugin_pushdown.py tests/ -v`
Expected: 新增 3 个 PASS;注意 `tests/test_plugin_duckdb.py::test_unknown_plugin_raises` 用的是 materialize(仍未实现)不受影响。全量 128 passed

- [ ] **Step 5: Commit**

```bash
git add backend/services/plugins/sql_pushdown.py backend/services/plugins/__init__.py tests/test_plugin_pushdown.py
git commit -m "feat: sql_pushdown 插件(连接下推/多语句/行数下限防呆)"
```

---

### Task 5: dependent 插件(跨工作流依赖)

**Files:**
- Create: `backend/services/plugins/dependent.py`
- Modify: `backend/services/plugins/__init__.py`(注册分支)
- Create: `tests/test_plugin_dependent.py`

- [ ] **Step 1: 写失败测试**

`tests/test_plugin_dependent.py`:

```python
from datetime import datetime

import pytest
from sqlalchemy.orm import sessionmaker

from backend.config import Settings
from backend.db import Base, make_engine
from backend.models import Workflow, WorkflowRun, WorkflowVersion
from backend.services.plugins import get_plugin
from backend.services.templating import build_context

CTX = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))


def _env_with_target(tmp_path, target_state):
    s = Settings(storage_dir=str(tmp_path))
    s.ensure_dirs()
    engine = make_engine(s.db_path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        wf = Workflow(project_id=None, name="upstream")
        db.add(wf)
        db.flush()
        ver = WorkflowVersion(workflow_id=wf.id, version_no=1, dag_json="{}")
        db.add(ver)
        db.flush()
        wf.current_version_id = ver.id
        if target_state:
            db.add(WorkflowRun(workflow_id=wf.id, version_id=ver.id, run_type="scheduled",
                               data_interval_start=datetime(2026, 6, 11),
                               data_interval_end=datetime(2026, 6, 12),
                               state=target_state))
        db.commit()
        wid = wf.id
    engine.dispose()
    return s, wid


def test_dependent_satisfied(tmp_path):
    env, wid = _env_with_target(tmp_path, "success")
    fn = get_plugin("dependent")
    assert fn({"workflow_id": wid}, CTX, env) == {"satisfied": True}


def test_dependent_not_satisfied_raises(tmp_path):
    env, wid = _env_with_target(tmp_path, "running")
    fn = get_plugin("dependent")
    with pytest.raises(RuntimeError, match="依赖未满足"):
        fn({"workflow_id": wid}, CTX, env)


def test_dependent_no_run_raises(tmp_path):
    env, wid = _env_with_target(tmp_path, None)
    fn = get_plugin("dependent")
    with pytest.raises(RuntimeError, match="依赖未满足"):
        fn({"workflow_id": wid}, CTX, env)
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_plugin_dependent.py -v`
Expected: FAIL("未实现")

- [ ] **Step 3: 写实现**

`backend/services/plugins/dependent.py`:

```python
"""dependent 插件:检查目标工作流在相同 data_interval 是否已成功。
未满足 → 抛错,由任务重试机制实现轮询等待(retries=轮询次数,retry_delay_sec=间隔)。
params: {workflow_id}"""
from datetime import datetime


def execute(params: dict, ctx: dict, env) -> dict:
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from ...db import make_engine
    from ...models import WorkflowRun

    target = int(params["workflow_id"])
    interval_start = datetime.strptime(ctx["data_interval_start"], "%Y-%m-%d %H:%M:%S")
    engine = make_engine(env.db_path)
    try:
        Session = sessionmaker(bind=engine)
        with Session() as db:
            ok = db.scalar(select(WorkflowRun.id).where(
                WorkflowRun.workflow_id == target,
                WorkflowRun.data_interval_start == interval_start,
                WorkflowRun.state == "success").limit(1))
    finally:
        engine.dispose()
    if not ok:
        raise RuntimeError(f"依赖未满足:工作流 {target} 在区间起点 "
                           f"{ctx['data_interval_start']} 尚未成功")
    return {"satisfied": True}
```

`backend/services/plugins/__init__.py` `get_plugin` 增加分支:

```python
    if task_type == "dependent":
        from .dependent import execute

        return execute
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_plugin_dependent.py tests/ -v`
Expected: 新增 3 个 PASS。注意:`test_unknown_plugin_raises` 断言 materialize"未实现"仍成立。全量 131 passed

- [ ] **Step 5: Commit**

```bash
git add backend/services/plugins/dependent.py backend/services/plugins/__init__.py tests/test_plugin_dependent.py
git commit -m "feat: dependent 插件(同周期跨工作流依赖,重试即轮询)"
```

---

### Task 6: 全量回归与收尾

- [ ] **Step 1:** Run `D:/conda/envs/scpy310/python.exe -m pytest tests/ -v` → 全部 PASS(约 131)
- [ ] **Step 2:** Run `D:/conda/envs/scpy310/python.exe -c "from backend.app import create_app; app = create_app(); print('routes:', len(app.routes))"` → 正常
- [ ] **Step 3:** `git status --short` → 空

---

## Self-Review 记录

- **Spec 覆盖**:spec §3 特征域(特征组/特征/血缘/生产即注册)全部落地;§6 插件中 sql_pushdown、dependent 落地;materialize 与在线查询归 Phase 2b。
- **占位符**:无。Task 1 测试中对 FK 的适配指令明确。
- **类型一致性**:`entity_keys_json` 存储/`entity_keys` 出参的命名差异在 `_fg_out` 统一;插件 env=Settings(db_path/storage_dir/offline_dir 均可达);`_exec_statements`/`_exec_scalar` 为模块级函数便于 monkeypatch;生产即注册以 (workflow_id, task_key) 关联,与 FeatureGroup 绑定校验一致。
