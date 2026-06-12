# Phase 1b-1:连接管理与调度域模型 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地调度系统的静态部分:数据源连接管理(Fernet 加密)、调度域四模型(Workflow/WorkflowVersion/WorkflowRun/TaskInstance)、DAG 校验、模板变量渲染、Workflow CRUD/版本化/上下线 API。

**Architecture:** 延续 Phase 1a 模式(FastAPI + SQLite WAL + Settings 注入)。工作流定义版本化:实例持有 version 快照;DAG 为节点+边 JSON;校验用 Kahn 拓扑。调度执行内核(tick/执行器/插件)属 Phase 1b-2,本计划不涉及。

**Tech Stack:** 同 Phase 1a + croniter(Cron 校验)+ zoneinfo(时区校验,py3.10 内置)。

**环境与命令约定:** 命令在 `D:\feature-platform` 下执行,Python 用 `D:/conda/envs/scpy310/python.exe`。开发分支:`feature/phase1b1-scheduling-models`(从 main 切出)。

---

### Task 1: Fernet 文本加解密 + Connection 模型

**Files:**
- Modify: `backend/services/secrets.py`(追加两个函数)
- Modify: `backend/models.py`(追加 Connection)
- Create: `tests/test_secrets_crypto.py`
- Modify: `tests/test_models.py`(追加 Connection 插入测试)

- [ ] **Step 1: 写失败测试**

`tests/test_secrets_crypto.py`:

```python
from backend.services.secrets import decrypt_text, encrypt_text


def test_encrypt_roundtrip(tmp_path):
    token = encrypt_text("p@ssw0rd", tmp_path)
    assert token != "p@ssw0rd"
    assert decrypt_text(token, tmp_path) == "p@ssw0rd"


def test_encrypt_differs_by_key(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    token = encrypt_text("x", a)
    import pytest
    from cryptography.fernet import InvalidToken

    with pytest.raises(InvalidToken):
        decrypt_text(token, b)
```

`tests/test_models.py` 末尾追加:

```python
def test_connection_model(tmp_path):
    from backend.models import Connection

    engine = make_engine(tmp_path / "meta.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(Connection(name="数仓", conn_type="spark", host="10.0.0.1", port=10000,
                          username="hive", password_enc="enc", database="dw"))
        db.commit()
        c = db.query(Connection).one()
        assert c.conn_type == "spark"
        assert c.port == 10000
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_secrets_crypto.py tests/test_models.py -v`
Expected: FAIL,`ImportError`(encrypt_text/Connection 不存在)

- [ ] **Step 3: 写实现**

`backend/services/secrets.py` 末尾追加:

```python
def encrypt_text(plaintext: str, storage_dir: Path) -> str:
    """连接密码等敏感文本加密(Fernet,密钥同 JWT)。"""
    return Fernet(secret_key(storage_dir)).encrypt(plaintext.encode("utf-8")).decode()


def decrypt_text(token: str, storage_dir: Path) -> str:
    return Fernet(secret_key(storage_dir)).decrypt(token.encode()).decode("utf-8")
```

`backend/models.py` 末尾追加:

```python
class Connection(Base):
    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    # 连接类型:mysql / spark(Spark ThriftServer,PyHive)
    conn_type: Mapped[str] = mapped_column(String(16), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str] = mapped_column(String(128), default="")
    password_enc: Mapped[str] = mapped_column(Text, default="")  # Fernet 加密存储
    database: Mapped[str] = mapped_column(String(128), default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_secrets_crypto.py tests/test_models.py -v`
Expected: PASS(新增 3 个,test_models 共 5 个)

- [ ] **Step 5: Commit**

```bash
git add backend/services/secrets.py backend/models.py tests/test_secrets_crypto.py tests/test_models.py
git commit -m "feat: Fernet 文本加解密与 Connection 模型"
```

---

### Task 2: 连接管理 API(管理员管理,登录用户可见)

**Files:**
- Create: `backend/services/connectors.py`
- Create: `backend/routers/connections.py`
- Modify: `backend/app.py`(挂载 connections 路由)
- Create: `tests/test_connections.py`

- [ ] **Step 1: 写失败测试**

`tests/test_connections.py`:

```python
def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_dev(client, admin_headers):
    client.post("/api/users", json={"username": "dev", "password": "dev123456", "role": "developer"}, headers=admin_headers)
    return _login(client, "dev", "dev123456")


CONN = {"name": "数仓", "conn_type": "spark", "host": "10.0.0.1", "port": 10000,
        "username": "hive", "password": "secret", "database": "dw"}


def test_create_and_list_masks_password(client, admin_headers):
    r = client.post("/api/connections", json=CONN, headers=admin_headers)
    assert r.status_code == 200
    out = client.get("/api/connections", headers=admin_headers).json()
    assert out[0]["has_password"] is True
    assert "password" not in out[0] and "password_enc" not in out[0]


def test_password_encrypted_in_db(client, admin_headers):
    client.post("/api/connections", json=CONN, headers=admin_headers)
    app = client.app
    from sqlalchemy import select

    from backend.models import Connection

    with app.state.sessionmaker() as db:
        c = db.scalar(select(Connection))
    assert c.password_enc != "secret" and len(c.password_enc) > 20


def test_developer_can_list_but_not_manage(client, admin_headers):
    client.post("/api/connections", json=CONN, headers=admin_headers)
    dev = _mk_dev(client, admin_headers)
    assert client.get("/api/connections", headers=dev).status_code == 200
    assert client.post("/api/connections", json={**CONN, "name": "x"}, headers=dev).status_code == 403
    assert client.delete("/api/connections/1", headers=dev).status_code == 403


def test_invalid_type_rejected(client, admin_headers):
    r = client.post("/api/connections", json={**CONN, "conn_type": "oracle"}, headers=admin_headers)
    assert r.status_code == 400


def test_patch_keeps_password_when_omitted(client, admin_headers):
    cid = client.post("/api/connections", json=CONN, headers=admin_headers).json()["id"]
    assert client.patch(f"/api/connections/{cid}", json={"host": "10.0.0.2"}, headers=admin_headers).status_code == 200
    from sqlalchemy import select

    from backend.models import Connection

    with client.app.state.sessionmaker() as db:
        c = db.scalar(select(Connection))
    assert c.host == "10.0.0.2"
    assert c.password_enc  # 未传 password 不清空


def test_test_endpoint_uses_connector(client, admin_headers, monkeypatch):
    cid = client.post("/api/connections", json=CONN, headers=admin_headers).json()["id"]
    calls = {}

    def fake_test(conn_type, host, port, username, password, database):
        calls.update(dict(conn_type=conn_type, password=password))

    from backend.services import connectors

    monkeypatch.setattr(connectors, "test_connection", fake_test)
    r = client.post(f"/api/connections/{cid}/test", headers=admin_headers)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert calls["password"] == "secret"  # 解密后传给连接器

    def fail_test(*a, **kw):
        raise RuntimeError("连接超时")

    monkeypatch.setattr(connectors, "test_connection", fail_test)
    assert client.post(f"/api/connections/{cid}/test", headers=admin_headers).status_code == 400


def test_delete(client, admin_headers):
    cid = client.post("/api/connections", json=CONN, headers=admin_headers).json()["id"]
    assert client.delete(f"/api/connections/{cid}", headers=admin_headers).status_code == 200
    assert client.get("/api/connections", headers=admin_headers).json() == []
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_connections.py -v`
Expected: FAIL,404

- [ ] **Step 3: 写实现**

`backend/services/connectors.py`:

```python
"""数据源连接器:测试连通。各任务插件自行取数,本模块只负责连通性探测。"""


def test_connection(conn_type: str, host: str, port: int, username: str,
                    password: str, database: str) -> None:
    """连通失败抛异常;成功返回 None。"""
    if conn_type == "mysql":
        import pymysql

        c = pymysql.connect(host=host, port=port, user=username, password=password,
                            database=database or None, connect_timeout=5)
        c.close()
    elif conn_type == "spark":
        from pyhive import hive

        c = hive.connect(host=host, port=port, username=username or None,
                         database=database or "default")
        c.close()
    else:
        raise ValueError(f"不支持的连接类型: {conn_type}")
```

`backend/routers/connections.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_current_user, get_db, get_settings, require_admin
from ..models import Connection
from ..services import connectors
from ..services.audit import record
from ..services.secrets import decrypt_text, encrypt_text

router = APIRouter(prefix="/connections", tags=["connections"])

CONN_TYPES = ("mysql", "spark")


class ConnectionIn(BaseModel):
    name: str
    conn_type: str
    host: str
    port: int
    username: str = ""
    password: str = ""
    database: str = ""


class ConnectionPatchIn(BaseModel):
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None
    database: str | None = None


def _conn_out(c: Connection) -> dict:
    return {"id": c.id, "name": c.name, "conn_type": c.conn_type, "host": c.host,
            "port": c.port, "username": c.username, "database": c.database,
            "has_password": bool(c.password_enc)}


@router.get("")
def list_connections(db=Depends(get_db), _=Depends(get_current_user)):
    """登录用户可见(配置任务时选用);密码不外发。"""
    return [_conn_out(c) for c in db.scalars(select(Connection).order_by(Connection.id)).all()]


@router.post("")
def create_connection(body: ConnectionIn, db=Depends(get_db),
                      settings=Depends(get_settings), admin=Depends(require_admin)):
    if body.conn_type not in CONN_TYPES:
        raise HTTPException(400, f"连接类型须为 {CONN_TYPES}")
    if db.scalar(select(Connection).where(Connection.name == body.name)):
        raise HTTPException(400, "连接名已存在")
    c = Connection(name=body.name, conn_type=body.conn_type, host=body.host, port=body.port,
                   username=body.username, database=body.database, created_by=admin.id,
                   password_enc=encrypt_text(body.password, settings.storage_dir) if body.password else "")
    db.add(c)
    record(db, admin, "create_connection", body.name)
    db.commit()
    return _conn_out(c)


@router.patch("/{cid}")
def patch_connection(cid: int, body: ConnectionPatchIn, db=Depends(get_db),
                     settings=Depends(get_settings), admin=Depends(require_admin)):
    c = db.get(Connection, cid)
    if c is None:
        raise HTTPException(404, "连接不存在")
    for field in ("host", "port", "username", "database"):
        v = getattr(body, field)
        if v is not None:
            setattr(c, field, v)
    if body.password is not None:
        c.password_enc = encrypt_text(body.password, settings.storage_dir) if body.password else ""
    record(db, admin, "update_connection", c.name)
    db.commit()
    return _conn_out(c)


@router.delete("/{cid}")
def delete_connection(cid: int, db=Depends(get_db), admin=Depends(require_admin)):
    c = db.get(Connection, cid)
    if c is None:
        raise HTTPException(404, "连接不存在")
    db.delete(c)
    record(db, admin, "delete_connection", c.name)
    db.commit()
    return {"ok": True}


@router.post("/{cid}/test")
def test_conn(cid: int, db=Depends(get_db), settings=Depends(get_settings), _=Depends(require_admin)):
    c = db.get(Connection, cid)
    if c is None:
        raise HTTPException(404, "连接不存在")
    password = decrypt_text(c.password_enc, settings.storage_dir) if c.password_enc else ""
    try:
        connectors.test_connection(c.conn_type, c.host, c.port, c.username, password, c.database)
    except Exception as e:  # noqa: BLE001  连通性探测失败统一转 400
        raise HTTPException(400, f"连接失败: {e}")
    return {"ok": True}
```

`backend/app.py` 修改——projects 路由挂载处下方增加:

```python
    from .routers import connections as connections_router

    app.include_router(connections_router.router, prefix="/api")
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_connections.py -v`
Expected: PASS(7 passed);全量 `tests/` 无回归

- [ ] **Step 5: Commit**

```bash
git add backend/services/connectors.py backend/routers/connections.py backend/app.py tests/test_connections.py
git commit -m "feat: 数据源连接管理 API(Fernet 加密/连通测试/权限分级)"
```

---

### Task 3: 调度域四模型

**Files:**
- Modify: `backend/models.py`(追加 4 个模型)
- Create: `tests/test_scheduling_models.py`

- [ ] **Step 1: 写失败测试**

`tests/test_scheduling_models.py`:

```python
from datetime import datetime

from sqlalchemy.orm import sessionmaker

from backend.db import Base, make_engine
from backend.models import (
    TaskInstance, User, Workflow, WorkflowRun, WorkflowVersion,
)


def _session(tmp_path):
    engine = make_engine(tmp_path / "meta.db")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_workflow_version_run_taskinstance(tmp_path):
    Session = _session(tmp_path)
    with Session() as db:
        u = User(username="a", password_hash="x", role="developer")
        db.add(u)
        db.flush()
        wf = Workflow(project_id=None, name="日特征", cron="0 2 * * *", created_by=u.id)
        db.add(wf)
        db.flush()
        ver = WorkflowVersion(workflow_id=wf.id, version_no=1, dag_json="{}", created_by=u.id)
        db.add(ver)
        db.flush()
        wf.current_version_id = ver.id
        run = WorkflowRun(workflow_id=wf.id, version_id=ver.id, run_type="scheduled",
                          data_interval_start=datetime(2026, 6, 11),
                          data_interval_end=datetime(2026, 6, 12))
        db.add(run)
        db.flush()
        ti = TaskInstance(run_id=run.id, task_key="t1", task_type="duckdb_sql",
                          params_json="{}", max_tries=3)
        db.add(ti)
        db.commit()
        assert db.query(Workflow).one().status == "offline"
        assert db.query(Workflow).one().failure_policy == "continue"
        assert db.query(WorkflowRun).one().state == "running"
        got = db.query(TaskInstance).one()
        assert got.state == "none"
        assert got.try_number == 0
        assert got.max_tries == 3


def test_workflow_name_unique_per_project(tmp_path):
    import pytest
    from sqlalchemy.exc import IntegrityError

    Session = _session(tmp_path)
    with Session() as db:
        db.add(Workflow(project_id=1, name="w"))
        db.commit()
        db.add(Workflow(project_id=1, name="w"))
        with pytest.raises(IntegrityError):
            db.commit()
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_scheduling_models.py -v`
Expected: FAIL,`ImportError`

- [ ] **Step 3: 写实现**

`backend/models.py` 末尾追加(注意文件头已有所需 import,仅 `UniqueConstraint` 在 Task 2 修复时已引入):

```python
class Workflow(Base):
    """工作流定义。修改 DAG 产生新 WorkflowVersion;实例持有版本快照。"""

    __tablename__ = "workflows"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_workflow_project_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    cron: Mapped[str | None] = mapped_column(String(64), nullable=True)  # None=仅手工触发
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    catchup: Mapped[bool] = mapped_column(Boolean, default=False)
    concurrency_limit: Mapped[int] = mapped_column(Integer, default=1)  # 同流最大并行实例数
    failure_policy: Mapped[str] = mapped_column(String(16), default="continue")  # continue/abort
    status: Mapped[str] = mapped_column(String(16), default="offline")  # online 才参与 cron 调度
    current_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # cron 水位
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    dag_json: Mapped[str] = mapped_column(Text, nullable=False)  # {"nodes":[...],"edges":[...]}
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WorkflowRun(Base):
    """工作流实例:一次触发。绑定 data_interval(Airflow 语义)与定义版本快照。"""

    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"), nullable=False)
    version_id: Mapped[int] = mapped_column(ForeignKey("workflow_versions.id"), nullable=False)
    run_type: Mapped[str] = mapped_column(String(16), nullable=False)  # scheduled/manual/backfill
    data_interval_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    data_interval_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="running")  # running/success/failed/stopped
    triggered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TaskInstance(Base):
    """任务实例:节点的一次执行。状态机见 spec §4.3;心跳供孤儿清理。"""

    __tablename__ = "task_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    task_key: Mapped[str] = mapped_column(String(64), nullable=False)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    params_json: Mapped[str] = mapped_column(Text, default="{}")  # 节点参数快照
    # none/scheduled/queued/running/success/failed/up_for_retry/upstream_failed/skipped
    state: Mapped[str] = mapped_column(String(20), default="none")
    try_number: Mapped[int] = mapped_column(Integer, default=0)
    max_tries: Mapped[int] = mapped_column(Integer, default=1)
    retry_delay_sec: Mapped[int] = mapped_column(Integer, default=60)
    timeout_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    log_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 插件产出(如行数)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_scheduling_models.py -v`
Expected: PASS(2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/models.py tests/test_scheduling_models.py
git commit -m "feat: 调度域模型(Workflow/Version/Run/TaskInstance)"
```

---

### Task 4: DAG 校验服务

**Files:**
- Create: `backend/services/dag.py`
- Create: `tests/test_dag.py`

- [ ] **Step 1: 写失败测试**

`tests/test_dag.py`:

```python
import pytest

from backend.services.dag import DagError, upstream_map, validate_dag


def _dag(nodes, edges):
    return {"nodes": nodes, "edges": edges}


N1 = {"key": "t1", "type": "duckdb_sql", "params": {"sql": "select 1"}}
N2 = {"key": "t2", "type": "python_script", "params": {"script": "s.py"}}
N3 = {"key": "t3", "type": "sql_pushdown", "params": {}}


def test_topo_order():
    order = validate_dag(_dag([N1, N2, N3], [["t1", "t2"], ["t2", "t3"]]))
    assert order == ["t1", "t2", "t3"]


def test_empty_nodes_rejected():
    with pytest.raises(DagError, match="至少需要一个节点"):
        validate_dag(_dag([], []))


def test_duplicate_key_rejected():
    with pytest.raises(DagError, match="重复"):
        validate_dag(_dag([N1, dict(N1)], []))


def test_unknown_type_rejected():
    with pytest.raises(DagError, match="类型非法"):
        validate_dag(_dag([{"key": "x", "type": "shell"}], []))


def test_edge_to_missing_node_rejected():
    with pytest.raises(DagError, match="不存在的节点"):
        validate_dag(_dag([N1], [["t1", "ghost"]]))


def test_self_loop_rejected():
    with pytest.raises(DagError, match="自环"):
        validate_dag(_dag([N1], [["t1", "t1"]]))


def test_cycle_rejected():
    with pytest.raises(DagError, match="存在环"):
        validate_dag(_dag([N1, N2], [["t1", "t2"], ["t2", "t1"]]))


def test_upstream_map():
    ups = upstream_map(_dag([N1, N2, N3], [["t1", "t3"], ["t2", "t3"]]))
    assert ups == {"t1": [], "t2": [], "t3": ["t1", "t2"]}
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_dag.py -v`
Expected: FAIL,`ModuleNotFoundError`

- [ ] **Step 3: 写实现**

`backend/services/dag.py`:

```python
"""DAG(节点+边 JSON)校验:key 唯一、类型合法、边引用存在、无环(Kahn)。"""

TASK_TYPES = ("sql_pushdown", "duckdb_sql", "python_script", "materialize", "dependent")


class DagError(ValueError):
    pass


def validate_dag(dag: dict) -> list[str]:
    """校验 DAG 并返回拓扑序 key 列表;非法抛 DagError。"""
    nodes = dag.get("nodes") or []
    edges = dag.get("edges") or []
    if not nodes:
        raise DagError("DAG 至少需要一个节点")
    keys = [n.get("key") for n in nodes]
    if any(not k for k in keys):
        raise DagError("节点 key 不能为空")
    if len(set(keys)) != len(keys):
        raise DagError("节点 key 重复")
    for n in nodes:
        if n.get("type") not in TASK_TYPES:
            raise DagError(f"节点 {n['key']} 类型非法: {n.get('type')}")
    key_set = set(keys)
    for e in edges:
        if len(e) != 2 or e[0] not in key_set or e[1] not in key_set:
            raise DagError(f"边引用不存在的节点: {e}")
        if e[0] == e[1]:
            raise DagError(f"不允许自环: {e}")
    downstream: dict[str, list[str]] = {k: [] for k in keys}
    indeg = {k: 0 for k in keys}
    for a, b in edges:
        downstream[a].append(b)
        indeg[b] += 1
    queue = [k for k in keys if indeg[k] == 0]
    order: list[str] = []
    while queue:
        k = queue.pop(0)
        order.append(k)
        for d in downstream[k]:
            indeg[d] -= 1
            if indeg[d] == 0:
                queue.append(d)
    if len(order) != len(keys):
        raise DagError("DAG 存在环")
    return order


def upstream_map(dag: dict) -> dict[str, list[str]]:
    """每个节点的直接上游 key 列表(调度器依赖推进用)。"""
    ups: dict[str, list[str]] = {n["key"]: [] for n in dag["nodes"]}
    for a, b in dag.get("edges") or []:
        ups[b].append(a)
    return ups
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_dag.py -v`
Expected: PASS(8 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/services/dag.py tests/test_dag.py
git commit -m "feat: DAG 校验(key/类型/边/环)与上游映射"
```

---

### Task 5: 模板变量渲染

**Files:**
- Create: `backend/services/templating.py`
- Create: `tests/test_templating.py`

- [ ] **Step 1: 写失败测试**

`tests/test_templating.py`:

```python
from datetime import datetime

import pytest

from backend.services.templating import build_context, render


def test_build_context():
    ctx = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))
    assert ctx["ds"] == "2026-06-11"
    assert ctx["ds_nodash"] == "20260611"
    assert ctx["data_interval_start"] == "2026-06-11 00:00:00"
    assert ctx["data_interval_end"] == "2026-06-12 00:00:00"


def test_render_replaces_with_spaces():
    ctx = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))
    sql = "insert overwrite t partition(dt='{{ ds }}') select * from s where etl_date='{{ds}}'"
    out = render(sql, ctx)
    assert out.count("2026-06-11") == 2
    assert "{{" not in out


def test_unknown_variable_raises():
    with pytest.raises(ValueError, match="未知模板变量"):
        render("select '{{ nope }}'", {"ds": "x"})


def test_no_template_passthrough():
    assert render("select 1", {"ds": "x"}) == "select 1"
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_templating.py -v`
Expected: FAIL,`ModuleNotFoundError`

- [ ] **Step 3: 写实现**

`backend/services/templating.py`:

```python
"""模板变量渲染:同一条 SQL/脚本参数既能日常调度也能补数。
变量:ds / ds_nodash(= data_interval_start 日期)、data_interval_start / data_interval_end。"""
import re
from datetime import datetime

_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def build_context(data_interval_start: datetime, data_interval_end: datetime) -> dict:
    return {
        "ds": data_interval_start.strftime("%Y-%m-%d"),
        "ds_nodash": data_interval_start.strftime("%Y%m%d"),
        "data_interval_start": data_interval_start.strftime("%Y-%m-%d %H:%M:%S"),
        "data_interval_end": data_interval_end.strftime("%Y-%m-%d %H:%M:%S"),
    }


def render(text: str, ctx: dict) -> str:
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name not in ctx:
            raise ValueError(f"未知模板变量: {name}")
        return str(ctx[name])

    return _PATTERN.sub(repl, text)
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_templating.py -v`
Expected: PASS(4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/services/templating.py tests/test_templating.py
git commit -m "feat: 模板变量渲染(ds/data_interval,未知变量报错)"
```

---

### Task 6: Workflow CRUD / 版本化 / 上下线 API

**Files:**
- Create: `backend/routers/workflows.py`
- Modify: `backend/app.py`(挂载 workflows 路由)
- Create: `tests/test_workflows.py`

- [ ] **Step 1: 写失败测试**

`tests/test_workflows.py`:

```python
import json


def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_dev_with_project(client, admin_headers, name="bob", project="特征工程"):
    client.post("/api/users", json={"username": name, "password": name + "123456", "role": "developer"}, headers=admin_headers)
    h = _login(client, name, name + "123456")
    pid = client.post("/api/projects", json={"name": project, "description": ""}, headers=h).json()["id"]
    return {**h, "X-Project-Id": str(pid)}, pid


DAG = {"nodes": [{"key": "t1", "type": "duckdb_sql", "params": {"sql": "select 1"},
                  "retries": 2, "retry_delay_sec": 30, "timeout_sec": 600}],
       "edges": []}

WF = {"name": "日特征", "description": "", "dag": DAG, "cron": "0 2 * * *",
      "timezone": "Asia/Shanghai", "catchup": False, "concurrency_limit": 1,
      "failure_policy": "continue"}


def test_create_get_list(client, admin_headers):
    h, pid = _mk_dev_with_project(client, admin_headers)
    r = client.post("/api/workflows", json=WF, headers=h)
    assert r.status_code == 200
    wid = r.json()["id"]
    assert r.json()["version_no"] == 1
    lst = client.get("/api/workflows", headers=h).json()
    assert [w["id"] for w in lst] == [wid]
    detail = client.get(f"/api/workflows/{wid}", headers=h).json()
    assert detail["dag"]["nodes"][0]["key"] == "t1"
    assert detail["status"] == "offline"


def test_requires_project_header(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    h2 = {k: v for k, v in h.items() if k != "X-Project-Id"}
    assert client.get("/api/workflows", headers=h2).status_code == 400


def test_invalid_dag_and_cron_rejected(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    bad_dag = {**WF, "dag": {"nodes": [{"key": "a", "type": "shell"}], "edges": []}}
    assert client.post("/api/workflows", json=bad_dag, headers=h).status_code == 400
    bad_cron = {**WF, "cron": "not a cron"}
    assert client.post("/api/workflows", json=bad_cron, headers=h).status_code == 400
    bad_policy = {**WF, "failure_policy": "explode"}
    assert client.post("/api/workflows", json=bad_policy, headers=h).status_code == 400
    bad_tz = {**WF, "timezone": "Mars/Olympus"}
    assert client.post("/api/workflows", json=bad_tz, headers=h).status_code == 400


def test_update_creates_new_version(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    wid = client.post("/api/workflows", json=WF, headers=h).json()["id"]
    dag2 = json.loads(json.dumps(DAG))
    dag2["nodes"].append({"key": "t2", "type": "python_script", "params": {"script": "x.py"}})
    dag2["edges"].append(["t1", "t2"])
    r = client.put(f"/api/workflows/{wid}", json={**WF, "dag": dag2}, headers=h)
    assert r.status_code == 200
    assert r.json()["version_no"] == 2
    versions = client.get(f"/api/workflows/{wid}/versions", headers=h).json()
    assert [v["version_no"] for v in versions] == [2, 1]


def test_online_requires_cron_and_audit(client, admin_headers):
    h, pid = _mk_dev_with_project(client, admin_headers)
    no_cron = {**WF, "name": "手工流", "cron": None}
    wid = client.post("/api/workflows", json=no_cron, headers=h).json()["id"]
    assert client.post(f"/api/workflows/{wid}/online", headers=h).status_code == 400
    wid2 = client.post("/api/workflows", json=WF, headers=h).json()["id"]
    assert client.post(f"/api/workflows/{wid2}/online", headers=h).status_code == 200
    assert client.get(f"/api/workflows/{wid2}", headers=h).json()["status"] == "online"
    assert client.post(f"/api/workflows/{wid2}/offline", headers=h).status_code == 200
    actions = [a["action"] for a in client.get(f"/api/projects/{pid}/audit", headers=h).json()]
    assert "online_workflow" in actions and "offline_workflow" in actions


def test_project_isolation(client, admin_headers):
    h1, _ = _mk_dev_with_project(client, admin_headers, name="bob", project="p1")
    h2, _ = _mk_dev_with_project(client, admin_headers, name="eve", project="p2")
    wid = client.post("/api/workflows", json=WF, headers=h1).json()["id"]
    assert client.get("/api/workflows", headers=h2).json() == []
    assert client.get(f"/api/workflows/{wid}", headers=h2).status_code == 404


def test_duplicate_name_rejected(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    client.post("/api/workflows", json=WF, headers=h)
    assert client.post("/api/workflows", json=WF, headers=h).status_code == 400
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_workflows.py -v`
Expected: FAIL,404

- [ ] **Step 3: 写实现**

`backend/routers/workflows.py`:

```python
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_current_user, get_db, get_project_id
from ..models import Workflow, WorkflowVersion
from ..services.audit import record
from ..services.dag import DagError, validate_dag

router = APIRouter(prefix="/workflows", tags=["workflows"])

FAILURE_POLICIES = ("continue", "abort")


class WorkflowIn(BaseModel):
    name: str
    description: str = ""
    dag: dict
    cron: str | None = None
    timezone: str = "Asia/Shanghai"
    catchup: bool = False
    concurrency_limit: int = 1
    failure_policy: str = "continue"


def _validate_meta(body: WorkflowIn) -> None:
    try:
        validate_dag(body.dag)
    except DagError as e:
        raise HTTPException(400, str(e))
    if body.cron is not None:
        from croniter import croniter

        if not croniter.is_valid(body.cron):
            raise HTTPException(400, f"Cron 表达式非法: {body.cron}")
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(body.timezone)
    except Exception:
        raise HTTPException(400, f"时区非法: {body.timezone}")
    if body.failure_policy not in FAILURE_POLICIES:
        raise HTTPException(400, f"失败策略须为 {FAILURE_POLICIES}")
    if body.concurrency_limit < 1:
        raise HTTPException(400, "并发上限须 ≥1")


def _get_in_project(db, wid: int, pid: int) -> Workflow:
    wf = db.get(Workflow, wid)
    if wf is None or wf.project_id != pid:
        raise HTTPException(404, "工作流不存在")
    return wf


def _wf_out(db, wf: Workflow, with_dag: bool = False) -> dict:
    ver = db.get(WorkflowVersion, wf.current_version_id)
    out = {"id": wf.id, "name": wf.name, "description": wf.description, "cron": wf.cron,
           "timezone": wf.timezone, "catchup": wf.catchup,
           "concurrency_limit": wf.concurrency_limit, "failure_policy": wf.failure_policy,
           "status": wf.status, "version_no": ver.version_no if ver else None,
           "created_at": wf.created_at.isoformat()}
    if with_dag and ver:
        out["dag"] = json.loads(ver.dag_json)
    return out


@router.get("")
def list_workflows(db=Depends(get_db), pid=Depends(get_project_id)):
    rows = db.scalars(select(Workflow).where(Workflow.project_id == pid).order_by(Workflow.id)).all()
    return [_wf_out(db, w) for w in rows]


@router.post("")
def create_workflow(body: WorkflowIn, db=Depends(get_db),
                    user=Depends(get_current_user), pid=Depends(get_project_id)):
    _validate_meta(body)
    if db.scalar(select(Workflow).where(Workflow.project_id == pid, Workflow.name == body.name)):
        raise HTTPException(400, "同项目下工作流名已存在")
    wf = Workflow(project_id=pid, name=body.name, description=body.description, cron=body.cron,
                  timezone=body.timezone, catchup=body.catchup,
                  concurrency_limit=body.concurrency_limit, failure_policy=body.failure_policy,
                  created_by=user.id)
    db.add(wf)
    db.flush()
    ver = WorkflowVersion(workflow_id=wf.id, version_no=1,
                          dag_json=json.dumps(body.dag, ensure_ascii=False), created_by=user.id)
    db.add(ver)
    db.flush()
    wf.current_version_id = ver.id
    record(db, user, "create_workflow", body.name, project_id=pid)
    db.commit()
    return _wf_out(db, wf)


@router.get("/{wid}")
def get_workflow(wid: int, db=Depends(get_db), pid=Depends(get_project_id)):
    return _wf_out(db, _get_in_project(db, wid, pid), with_dag=True)


@router.put("/{wid}")
def update_workflow(wid: int, body: WorkflowIn, db=Depends(get_db),
                    user=Depends(get_current_user), pid=Depends(get_project_id)):
    wf = _get_in_project(db, wid, pid)
    _validate_meta(body)
    dup = db.scalar(select(Workflow).where(
        Workflow.project_id == pid, Workflow.name == body.name, Workflow.id != wid))
    if dup:
        raise HTTPException(400, "同项目下工作流名已存在")
    wf.name, wf.description, wf.cron = body.name, body.description, body.cron
    wf.timezone, wf.catchup = body.timezone, body.catchup
    wf.concurrency_limit, wf.failure_policy = body.concurrency_limit, body.failure_policy
    cur = db.get(WorkflowVersion, wf.current_version_id)
    new_dag = json.dumps(body.dag, ensure_ascii=False)
    if cur is None or cur.dag_json != new_dag:
        next_no = (db.scalar(select(WorkflowVersion.version_no)
                             .where(WorkflowVersion.workflow_id == wid)
                             .order_by(WorkflowVersion.version_no.desc())) or 0) + 1
        ver = WorkflowVersion(workflow_id=wid, version_no=next_no, dag_json=new_dag,
                              created_by=user.id)
        db.add(ver)
        db.flush()
        wf.current_version_id = ver.id
    record(db, user, "update_workflow", wf.name, project_id=pid)
    db.commit()
    return _wf_out(db, wf)


@router.get("/{wid}/versions")
def list_versions(wid: int, db=Depends(get_db), pid=Depends(get_project_id)):
    _get_in_project(db, wid, pid)
    rows = db.scalars(select(WorkflowVersion).where(WorkflowVersion.workflow_id == wid)
                      .order_by(WorkflowVersion.version_no.desc())).all()
    return [{"version_no": v.version_no, "created_by": v.created_by,
             "created_at": v.created_at.isoformat()} for v in rows]


@router.post("/{wid}/online")
def online(wid: int, db=Depends(get_db), user=Depends(get_current_user), pid=Depends(get_project_id)):
    wf = _get_in_project(db, wid, pid)
    if not wf.cron:
        raise HTTPException(400, "未配置 Cron,无法上线定时调度")
    wf.status = "online"
    record(db, user, "online_workflow", wf.name, project_id=pid)
    db.commit()
    return {"ok": True}


@router.post("/{wid}/offline")
def offline(wid: int, db=Depends(get_db), user=Depends(get_current_user), pid=Depends(get_project_id)):
    wf = _get_in_project(db, wid, pid)
    wf.status = "offline"
    record(db, user, "offline_workflow", wf.name, project_id=pid)
    db.commit()
    return {"ok": True}
```

`backend/app.py` 修改——connections 路由挂载处下方增加:

```python
    from .routers import workflows as workflows_router

    app.include_router(workflows_router.router, prefix="/api")
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_workflows.py -v`
Expected: PASS(7 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/routers/workflows.py backend/app.py tests/test_workflows.py
git commit -m "feat: 工作流 CRUD/版本化/上下线 API(项目隔离+审计)"
```

---

### Task 7: 全量回归与收尾

- [ ] **Step 1: 全量测试**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/ -v`
Expected: 全部 PASS(预计 25 + 约 28 新增 ≈ 53 个)

- [ ] **Step 2: 启动冒烟**

Run: `D:/conda/envs/scpy310/python.exe -c "from backend.app import create_app; app = create_app(); print('routes:', len(app.routes))"`
Expected: routes 数量比 Phase 1a(17)增加,无异常

- [ ] **Step 3: 工作区检查**

Run: `git status --short`
Expected: 输出为空

---

## Self-Review 记录

- **Spec 覆盖**:本计划覆盖 spec §6 连接管理、§4.1 三层模型(静态部分)、§4.2 模板变量、§4.4 节点配置(参数快照结构);tick 内核/执行器/插件/补数/手工操作(§4.5、§5、§6 执行部分)归 Phase 1b-2。
- **占位符**:无 TBD;所有步骤含完整代码与命令。
- **类型一致性**:`UniqueConstraint` 已在 Task 2 修复时引入 models.py 的 import;`validate_dag` 返回拓扑序供 1b-2 调度器复用;`upstream_map` 供 1b-2 依赖推进;TaskInstance 字段与 spec §4.3 状态机一致;`get_project_id` 来自 Phase 1a deps.py。
