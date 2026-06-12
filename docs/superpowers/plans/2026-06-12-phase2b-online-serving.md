# Phase 2b:在线特征服务 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实时特征能力闭环:online_store.db(独立 SQLite KV)、materialize 插件(水位增量幂等 upsert)、API Key 管理、在线特征查询 API(TTL 过期标记)、端到端(调度产出 → 物化 → 在线查询)。

**Architecture:** 在线存储用 stdlib sqlite3 直连(WAL,不进 ORM——KV 场景轻量);entity_key = 多主键值按 `|` 拼接;payload 存整行 JSON。**event_time 口径:统一字符串比较,要求 ISO 格式(YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS),水位/增量/TTL 均基于此;非 ISO 格式视为无 event_time(全量物化、不判过期)。** 在线查询双端点共享服务逻辑:`/online-features`(X-API-Key,供线上系统)与 `/feature-groups/{fid}/online-debug`(JWT 项目成员,供调试台)。

**约定:** 命令在 `D:\feature-platform`,Python `D:/conda/envs/scpy310/python.exe`。分支 `feature/phase2b-online-serving`(从 main 切出)。

---

### Task 1: online_store 服务

**Files:**
- Create: `backend/services/online_store.py`
- Create: `tests/test_online_store.py`

- [ ] **Step 1: 写失败测试**

`tests/test_online_store.py`:

```python
from backend.services.online_store import ensure_schema, query, upsert


def test_upsert_and_query(tmp_path):
    db = tmp_path / "online.db"
    ensure_schema(db)
    rows = [{"cust_no": "C1", "dt": "2026-06-11", "amt": 100.5},
            {"cust_no": "C2", "dt": "2026-06-11", "amt": 7}]
    n, max_et = upsert(db, fg_id=1, rows=rows, entity_keys=["cust_no"], event_time_col="dt")
    assert n == 2 and max_et == "2026-06-11"
    got = query(db, 1, "C1")
    assert got["payload"]["amt"] == 100.5
    assert got["event_time"] == "2026-06-11"
    assert got["updated_at"]
    assert query(db, 1, "GHOST") is None
    assert query(db, 2, "C1") is None  # 特征组隔离


def test_upsert_idempotent_overwrite(tmp_path):
    db = tmp_path / "online.db"
    ensure_schema(db)
    upsert(db, 1, [{"k": "a", "v": 1}], ["k"], None)
    n, max_et = upsert(db, 1, [{"k": "a", "v": 2}], ["k"], None)
    assert n == 1 and max_et is None
    assert query(db, 1, "a")["payload"]["v"] == 2  # 覆盖而非重复


def test_composite_entity_key(tmp_path):
    db = tmp_path / "online.db"
    ensure_schema(db)
    upsert(db, 1, [{"a": "x", "b": 1, "v": 9}], ["a", "b"], None)
    assert query(db, 1, "x|1")["payload"]["v"] == 9


def test_missing_entity_key_raises(tmp_path):
    import pytest

    db = tmp_path / "online.db"
    ensure_schema(db)
    with pytest.raises(ValueError, match="缺少主键列"):
        upsert(db, 1, [{"v": 1}], ["k"], None)
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_online_store.py -v`
Expected: FAIL,`ModuleNotFoundError`

- [ ] **Step 3: 写实现**

`backend/services/online_store.py`:

```python
"""在线特征存储:独立 SQLite(WAL)KV 表,stdlib sqlite3 直连。
entity_key = 多主键值按 '|' 拼接;payload 存整行 JSON;event_time 为 ISO 字符串(口径见计划头)。"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path


def _connect(path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def ensure_schema(path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = _connect(path)
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS online_features(
                feature_group_id INTEGER NOT NULL,
                entity_key TEXT NOT NULL,
                payload TEXT NOT NULL,
                event_time TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(feature_group_id, entity_key))
        """)
        con.commit()
    finally:
        con.close()


def build_entity_key(row: dict, entity_keys: list[str]) -> str:
    parts = []
    for k in entity_keys:
        if k not in row or row[k] is None:
            raise ValueError(f"缺少主键列: {k}")
        parts.append(str(row[k]))
    return "|".join(parts)


def upsert(path, fg_id: int, rows: list[dict], entity_keys: list[str],
           event_time_col: str | None) -> tuple[int, str | None]:
    """幂等写入(INSERT OR REPLACE)。返回 (写入行数, 本批最大 event_time 字符串或 None)。"""
    ensure_schema(path)
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    max_et: str | None = None
    con = _connect(path)
    try:
        for r in rows:
            ek = build_entity_key(r, entity_keys)
            et = None
            if event_time_col and r.get(event_time_col) is not None:
                et = str(r[event_time_col])
                if max_et is None or et > max_et:
                    max_et = et
            con.execute(
                "INSERT OR REPLACE INTO online_features VALUES (?,?,?,?,?)",
                (fg_id, ek, json.dumps(r, ensure_ascii=False, default=str), et, now))
        con.commit()
    finally:
        con.close()
    return len(rows), max_et


def query(path, fg_id: int, entity_key: str) -> dict | None:
    ensure_schema(path)
    con = _connect(path)
    try:
        row = con.execute(
            "SELECT payload, event_time, updated_at FROM online_features "
            "WHERE feature_group_id=? AND entity_key=?", (fg_id, entity_key)).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    return {"payload": json.loads(row[0]), "event_time": row[1], "updated_at": row[2]}
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_online_store.py tests/ -v`
Expected: 新增 4 个 PASS,全量 139 passed

- [ ] **Step 5: Commit**

```bash
git add backend/services/online_store.py tests/test_online_store.py
git commit -m "feat: 在线特征存储(独立 SQLite KV/幂等 upsert/复合主键)"
```

---

### Task 2: ApiKey 模型与管理 API

**Files:**
- Modify: `backend/models.py`(追加 ApiKey)
- Create: `backend/routers/api_keys.py`
- Modify: `backend/app.py`(挂载)
- Create: `tests/test_api_keys.py`

- [ ] **Step 1: 写失败测试**

`tests/test_api_keys.py`:

```python
def test_create_returns_plaintext_once(client, admin_headers):
    r = client.post("/api/api-keys", json={"name": "risk-engine"}, headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body["key"]) >= 32  # 明文仅此一次
    lst = client.get("/api/api-keys", headers=admin_headers).json()
    assert lst[0]["name"] == "risk-engine"
    assert "key" not in lst[0] and "key_hash" not in lst[0]
    assert lst[0]["is_active"] is True and lst[0]["calls"] == 0


def test_duplicate_name_rejected(client, admin_headers):
    client.post("/api/api-keys", json={"name": "k"}, headers=admin_headers)
    assert client.post("/api/api-keys", json={"name": "k"},
                       headers=admin_headers).status_code == 400


def test_disable(client, admin_headers):
    kid = client.post("/api/api-keys", json={"name": "k"},
                      headers=admin_headers).json()["id"]
    assert client.post(f"/api/api-keys/{kid}/disable",
                       headers=admin_headers).status_code == 200
    lst = client.get("/api/api-keys", headers=admin_headers).json()
    assert lst[0]["is_active"] is False


def test_admin_only(client, admin_headers):
    client.post("/api/users", json={"username": "dev", "password": "dev123456",
                                    "role": "developer"}, headers=admin_headers)
    r = client.post("/api/auth/login", json={"username": "dev", "password": "dev123456"})
    dev = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.post("/api/api-keys", json={"name": "x"}, headers=dev).status_code == 403
    assert client.get("/api/api-keys", headers=dev).status_code == 403
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_api_keys.py -v`
Expected: FAIL,404

- [ ] **Step 3: 写实现**

`backend/models.py` 末尾追加:

```python
class ApiKey(Base):
    """在线查询 API Key:仅存 sha256 哈希,明文只在创建时返回一次。"""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)  # 调用量统计(按请求次数)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

`backend/routers/api_keys.py`:

```python
import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_db, require_admin
from ..models import ApiKey
from ..services.audit import record

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class ApiKeyIn(BaseModel):
    name: str


def _out(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "is_active": k.is_active, "calls": k.calls,
            "created_at": k.created_at.isoformat()}


@router.get("")
def list_keys(db=Depends(get_db), _=Depends(require_admin)):
    return [_out(k) for k in db.scalars(select(ApiKey).order_by(ApiKey.id)).all()]


@router.post("")
def create_key(body: ApiKeyIn, db=Depends(get_db), admin=Depends(require_admin)):
    if db.scalar(select(ApiKey).where(ApiKey.name == body.name)):
        raise HTTPException(400, "名称已存在")
    plaintext = secrets.token_urlsafe(32)
    k = ApiKey(name=body.name, key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
               created_by=admin.id)
    db.add(k)
    record(db, admin, "create_api_key", body.name)
    db.commit()
    return {**_out(k), "key": plaintext}  # 明文仅此一次


@router.post("/{kid}/disable")
def disable_key(kid: int, db=Depends(get_db), admin=Depends(require_admin)):
    k = db.get(ApiKey, kid)
    if k is None:
        raise HTTPException(404, "不存在")
    k.is_active = False
    record(db, admin, "disable_api_key", k.name)
    db.commit()
    return {"ok": True}
```

`backend/app.py` 挂载(feature_groups 下方):

```python
    from .routers import api_keys as api_keys_router

    app.include_router(api_keys_router.router, prefix="/api")
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_api_keys.py tests/ -v`
Expected: 新增 4 个 PASS,全量 143 passed

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/routers/api_keys.py backend/app.py tests/test_api_keys.py
git commit -m "feat: API Key 管理(sha256 存储/明文一次/禁用/审计)"
```

---

### Task 3: materialize 插件

**Files:**
- Create: `backend/services/plugins/materialize.py`
- Modify: `backend/services/plugins/__init__.py`(注册分支)
- Modify: `tests/test_plugin_duckdb.py`(`test_unknown_plugin_raises` 更新:materialize 已实现,改为仅断言未知类型)
- Create: `tests/test_plugin_materialize.py`

- [ ] **Step 1: 写失败测试**

`tests/test_plugin_duckdb.py` 中 `test_unknown_plugin_raises` 改为:

```python
def test_unknown_plugin_raises():
    with pytest.raises(PluginError, match="未知"):
        get_plugin("nonsense")
```

`tests/test_plugin_materialize.py`:

```python
import json
from datetime import datetime

import duckdb
import pytest
from sqlalchemy.orm import sessionmaker

from backend.config import Settings
from backend.db import Base, make_engine
from backend.models import FeatureGroup
from backend.services.online_store import query
from backend.services.plugins import get_plugin
from backend.services.templating import build_context

CTX = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))


def _setup(tmp_path, *, event_time_col="dt", watermark=None, kind="parquet"):
    s = Settings(storage_dir=str(tmp_path))
    s.ensure_dirs()
    engine = make_engine(s.db_path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        fg = FeatureGroup(project_id=None, name="g", entity_keys_json='["cust_no"]',
                          event_time_col=event_time_col, ttl_days=7, online_enabled=True,
                          offline_kind=kind, offline_location="g",
                          materialize_watermark=watermark)
        db.add(fg)
        db.commit()
        fgid = fg.id
    engine.dispose()
    return s, fgid, Session


def _write_parquet(env, rows_sql):
    out = env.offline_dir / "g"
    out.mkdir(parents=True, exist_ok=True)
    duckdb.sql(f"COPY ({rows_sql}) TO '{(out / 'p.parquet').as_posix()}' (FORMAT PARQUET)")


def test_materialize_parquet_full_then_incremental(tmp_path):
    env, fgid, Session = _setup(tmp_path)
    _write_parquet(env, "select 'C1' cust_no, '2026-06-10' dt, 1 v "
                        "union all select 'C2', '2026-06-11', 2")
    fn = get_plugin("materialize")
    r = fn({"feature_group_id": fgid}, CTX, env)
    assert r["rows"] == 2
    assert query(env.online_db_path, fgid, "C2")["payload"]["v"] == 2
    with Session() as db:
        assert db.get(FeatureGroup, fgid).materialize_watermark == datetime(2026, 6, 11)
    # 第二次:只有新数据进入
    _write_parquet(env, "select 'C3' cust_no, '2026-06-12' dt, 3 v "
                        "union all select 'C1', '2026-06-09', 99")
    r2 = fn({"feature_group_id": fgid}, CTX, env)
    assert r2["rows"] == 1  # 仅 C3(C1 的 06-09 早于水位)
    assert query(env.online_db_path, fgid, "C1")["payload"]["v"] == 1  # 未被旧数据覆盖


def test_materialize_requires_online_enabled(tmp_path):
    env, fgid, Session = _setup(tmp_path)
    with Session() as db:
        db.get(FeatureGroup, fgid).online_enabled = False
        db.commit()
    fn = get_plugin("materialize")
    with pytest.raises(ValueError, match="未启用在线"):
        fn({"feature_group_id": fgid}, CTX, env)


def test_materialize_missing_fg(tmp_path):
    env, _, _ = _setup(tmp_path)
    fn = get_plugin("materialize")
    with pytest.raises(ValueError, match="特征组不存在"):
        fn({"feature_group_id": 999}, CTX, env)


def test_materialize_warehouse_via_fetch(tmp_path, monkeypatch):
    env, fgid, Session = _setup(tmp_path, kind="warehouse")
    with Session() as db:
        db.get(FeatureGroup, fgid).offline_location = "dw.t_cust"
        db.commit()
    from backend.services.plugins import materialize as mat

    captured = {}

    def fake_fetch(conn_info, sql):
        captured["sql"] = sql
        return ["cust_no", "dt", "v"], [("C9", "2026-06-11", 7)]

    monkeypatch.setattr(mat, "_fetch_rows", fake_fetch)
    monkeypatch.setattr(mat, "_connection_info",
                        lambda params, env: ("mysql", "h", 3306, "u", "p", "dw"))
    fn = get_plugin("materialize")
    r = fn({"feature_group_id": fgid, "connection_id": 1}, CTX, env)
    assert r["rows"] == 1
    assert "dw.t_cust" in captured["sql"]
    assert query(env.online_db_path, fgid, "C9")["payload"]["v"] == 7
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_plugin_materialize.py -v`
Expected: FAIL("未实现")

- [ ] **Step 3: 写实现**

`backend/services/plugins/materialize.py`:

```python
"""materialize 插件:离线特征 → 在线存储,按水位(event_time 字符串序)增量,幂等 upsert。
params: {feature_group_id, connection_id?(warehouse 来源必填)}
parquet 来源:duckdb 读 offline_dir/<location>/*.parquet;
warehouse 来源:经连接 SELECT(复用 sql_pushdown 连接抽象)。
event_time 口径:ISO 字符串比较;水位仅在可解析为日期/时间时推进。"""
import json
from datetime import datetime

from sqlalchemy.orm import sessionmaker

from ..online_store import upsert
from .sql_pushdown import _connection_info


def _fetch_rows(conn_info: tuple, sql: str) -> tuple[list[str], list[tuple]]:
    """warehouse 取数:返回 (列名列表, 行元组列表)。"""
    conn_type, host, port, username, password, database = conn_info
    if conn_type == "mysql":
        import pymysql

        conn = pymysql.connect(host=host, port=port, user=username, password=password,
                               database=database or None, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                cols = [d[0] for d in cur.description]
                return cols, cur.fetchall()
        finally:
            conn.close()
    elif conn_type == "spark":
        from pyhive import hive

        conn = hive.connect(host=host, port=port, username=username or None,
                            database=database or "default")
        try:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                cols = [d[0].split(".")[-1] for d in cur.description]
                return cols, cur.fetchall()
            finally:
                cur.close()
        finally:
            conn.close()
    raise ValueError(f"不支持的连接类型: {conn_type}")


def _parse_watermark(et: str | None) -> datetime | None:
    if not et:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(et, fmt)
        except ValueError:
            continue
    return None


def execute(params: dict, ctx: dict, env) -> dict:
    from ...db import make_engine
    from ...models import FeatureGroup

    engine = make_engine(env.db_path)
    try:
        Session = sessionmaker(bind=engine)
        with Session() as db:
            fg = db.get(FeatureGroup, params["feature_group_id"])
            if fg is None:
                raise ValueError("特征组不存在")
            if not fg.online_enabled:
                raise ValueError(f"特征组 {fg.name} 未启用在线服务")
            entity_keys = json.loads(fg.entity_keys_json)
            et_col = fg.event_time_col
            wm = fg.materialize_watermark
            wm_str = (wm.strftime("%Y-%m-%d %H:%M:%S") if wm and (wm.hour or wm.minute or wm.second)
                      else (wm.strftime("%Y-%m-%d") if wm else None))
            kind, location = fg.offline_kind, fg.offline_location

        rows = _load_rows(params, env, kind, location, et_col, wm_str)
        n, max_et = upsert(env.online_db_path, params["feature_group_id"], rows,
                           entity_keys, et_col)
        new_wm = _parse_watermark(max_et)
        if new_wm is not None:
            with Session() as db:
                fg = db.get(FeatureGroup, params["feature_group_id"])
                if fg.materialize_watermark is None or new_wm > fg.materialize_watermark:
                    fg.materialize_watermark = new_wm
                    db.commit()
        return {"rows": n, "watermark": max_et}
    finally:
        engine.dispose()


def _load_rows(params: dict, env, kind: str, location: str,
               et_col: str | None, wm_str: str | None) -> list[dict]:
    if kind == "parquet":
        import duckdb

        pattern = (env.offline_dir / location / "*.parquet").as_posix()
        sql = f"select * from read_parquet('{pattern}')"
        if et_col and wm_str:
            sql += f" where cast(\"{et_col}\" as varchar) > '{wm_str}'"
        rel = duckdb.sql(sql)
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, r)) for r in rel.fetchall()]
    if kind == "warehouse":
        if not params.get("connection_id"):
            raise ValueError("warehouse 来源须提供 connection_id")
        info = _connection_info(params, env)
        sql = f"select * from {location}"
        if et_col and wm_str:
            sql += f" where {et_col} > '{wm_str}'"
        cols, tuples = _fetch_rows(info, sql)
        return [dict(zip(cols, r)) for r in tuples]
    raise ValueError(f"未知离线落地类型: {kind}")
```

`backend/services/plugins/__init__.py` `get_plugin` 增加分支(dependent 后):

```python
    if task_type == "materialize":
        from .materialize import execute

        return execute
```

同时 `__init__.py` 中"插件未实现"兜底分支此时已无实际命中(五类全实现),保留该分支以防 TASK_TYPES 扩展时回归。

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_plugin_materialize.py tests/test_plugin_duckdb.py tests/ -v`
Expected: 新增 4 个 PASS + 修改后的 unknown 测试 PASS,全量 147 passed

- [ ] **Step 5: Commit**

```bash
git add backend/services/plugins/materialize.py backend/services/plugins/__init__.py tests/test_plugin_materialize.py tests/test_plugin_duckdb.py
git commit -m "feat: materialize 插件(水位增量/parquet 真跑/warehouse 取数抽象/幂等)"
```

---

### Task 4: 在线查询 API(API Key + 调试端点)

**Files:**
- Create: `backend/routers/online.py`
- Modify: `backend/app.py`(挂载;`/api/online-features` 不加 JWT)
- Create: `tests/test_online_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_online_api.py`:

```python
from datetime import datetime, timedelta

from backend.services.online_store import upsert


def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_fg(client, admin_headers, ttl_days=7):
    client.post("/api/users", json={"username": "bob", "password": "bob123456",
                                    "role": "developer"}, headers=admin_headers)
    h = _login(client, "bob", "bob123456")
    pid = client.post("/api/projects", json={"name": "p1", "description": ""},
                      headers=h).json()["id"]
    h = {**h, "X-Project-Id": str(pid)}
    fg = client.post("/api/feature-groups", json={
        "name": "g", "description": "", "entity_keys": ["cust_no"],
        "event_time_col": "dt", "ttl_days": ttl_days, "online_enabled": True,
        "offline_kind": "parquet", "offline_location": "g",
        "features": [{"name": "v", "dtype": "double", "description": ""}],
        "upstream_tables": []}, headers=h).json()
    return h, fg["id"]


def _mk_key(client, admin_headers):
    return client.post("/api/api-keys", json={"name": "k"},
                       headers=admin_headers).json()["key"]


def _seed_online(client, fgid, dt):
    path = client.app.state.settings.online_db_path
    upsert(path, fgid, [{"cust_no": "C1", "dt": dt, "v": 5}], ["cust_no"], "dt")


def test_query_ok_and_calls_counted(client, admin_headers):
    h, fgid = _mk_fg(client, admin_headers)
    key = _mk_key(client, admin_headers)
    _seed_online(client, fgid, datetime.utcnow().strftime("%Y-%m-%d"))
    r = client.post("/api/online-features", headers={"X-API-Key": key},
                    json={"feature_group_id": fgid, "keys": [{"cust_no": "C1"},
                                                             {"cust_no": "GHOST"}]})
    assert r.status_code == 200
    out = r.json()["results"]
    assert out[0]["values"]["v"] == 5 and out[0]["expired"] is False
    assert out[1]["values"] is None and out[1]["expired"] is False  # miss
    lst = client.get("/api/api-keys", headers=admin_headers).json()
    assert lst[0]["calls"] == 1


def test_ttl_expired_marked(client, admin_headers):
    h, fgid = _mk_fg(client, admin_headers, ttl_days=7)
    key = _mk_key(client, admin_headers)
    old = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    _seed_online(client, fgid, old)
    r = client.post("/api/online-features", headers={"X-API-Key": key},
                    json={"feature_group_id": fgid, "keys": [{"cust_no": "C1"}]})
    out = r.json()["results"][0]
    assert out["values"] is None and out["expired"] is True


def test_auth_required_and_disabled_key(client, admin_headers):
    h, fgid = _mk_fg(client, admin_headers)
    assert client.post("/api/online-features",
                       json={"feature_group_id": fgid, "keys": []}).status_code == 401
    assert client.post("/api/online-features", headers={"X-API-Key": "wrong"},
                       json={"feature_group_id": fgid, "keys": []}).status_code == 401
    kid_key = client.post("/api/api-keys", json={"name": "k2"}, headers=admin_headers).json()
    client.post(f"/api/api-keys/{kid_key['id']}/disable", headers=admin_headers)
    assert client.post("/api/online-features", headers={"X-API-Key": kid_key["key"]},
                       json={"feature_group_id": fgid, "keys": []}).status_code == 401


def test_offline_only_group_rejected(client, admin_headers):
    h, _ = _mk_fg(client, admin_headers)
    fg2 = client.post("/api/feature-groups", json={
        "name": "off", "description": "", "entity_keys": ["k"], "event_time_col": None,
        "ttl_days": None, "online_enabled": False, "offline_kind": "parquet",
        "offline_location": "off", "features": [{"name": "v", "dtype": "int",
                                                 "description": ""}],
        "upstream_tables": []}, headers=h).json()
    key = _mk_key(client, admin_headers)
    r = client.post("/api/online-features", headers={"X-API-Key": key},
                    json={"feature_group_id": fg2["id"], "keys": [{"k": "1"}]})
    assert r.status_code == 400


def test_debug_endpoint_jwt(client, admin_headers):
    h, fgid = _mk_fg(client, admin_headers)
    _seed_online(client, fgid, datetime.utcnow().strftime("%Y-%m-%d"))
    r = client.post(f"/api/feature-groups/{fgid}/online-debug",
                    json={"keys": [{"cust_no": "C1"}]}, headers=h)
    assert r.status_code == 200
    assert r.json()["results"][0]["values"]["v"] == 5
    # 无 JWT 拒绝
    assert client.post(f"/api/feature-groups/{fgid}/online-debug",
                       json={"keys": []}).status_code == 401
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_online_api.py -v`
Expected: FAIL,404

- [ ] **Step 3: 写实现**

`backend/routers/online.py`:

```python
"""在线特征查询:/online-features 供线上系统(X-API-Key);
/feature-groups/{fid}/online-debug 供平台用户调试(JWT+项目成员)。共享查询逻辑。"""
import hashlib
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_db, get_project_id, get_settings
from ..models import ApiKey, FeatureGroup
from ..services.online_store import build_entity_key, query

router = APIRouter(tags=["online"])


class OnlineQueryIn(BaseModel):
    feature_group_id: int
    keys: list[dict]


class DebugQueryIn(BaseModel):
    keys: list[dict]


def _query_fg(db, settings, fg: FeatureGroup, keys: list[dict]) -> list[dict]:
    if not fg.online_enabled:
        raise HTTPException(400, "该特征组未启用在线服务")
    entity_keys = json.loads(fg.entity_keys_json)
    now = datetime.utcnow()
    results = []
    for k in keys:
        try:
            ek = build_entity_key(k, entity_keys)
        except ValueError as e:
            raise HTTPException(400, str(e))
        row = query(settings.online_db_path, fg.id, ek)
        if row is None:
            results.append({"key": k, "values": None, "expired": False})
            continue
        expired = False
        if fg.ttl_days and row["event_time"]:
            et = _parse_dt(row["event_time"])
            if et is not None and et < now - timedelta(days=fg.ttl_days):
                expired = True
        results.append({"key": k, "values": None if expired else row["payload"],
                        "expired": expired,
                        "event_time": row["event_time"], "updated_at": row["updated_at"]})
    return results


def _parse_dt(s: str):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


@router.post("/online-features")
def online_features(body: OnlineQueryIn, db=Depends(get_db), settings=Depends(get_settings),
                    x_api_key: str | None = Header(default=None)):
    if not x_api_key:
        raise HTTPException(401, "缺少 X-API-Key")
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    ak = db.scalar(select(ApiKey).where(ApiKey.key_hash == key_hash,
                                        ApiKey.is_active.is_(True)))
    if ak is None:
        raise HTTPException(401, "API Key 无效或已禁用")
    fg = db.get(FeatureGroup, body.feature_group_id)
    if fg is None:
        raise HTTPException(404, "特征组不存在")
    results = _query_fg(db, settings, fg, body.keys)
    ak.calls += 1
    db.commit()
    return {"feature_group": fg.name, "version": fg.version, "results": results}


@router.post("/feature-groups/{fid}/online-debug")
def online_debug(fid: int, body: DebugQueryIn, db=Depends(get_db),
                 settings=Depends(get_settings), pid=Depends(get_project_id)):
    fg = db.get(FeatureGroup, fid)
    if fg is None or fg.project_id != pid:
        raise HTTPException(404, "特征组不存在")
    return {"feature_group": fg.name, "version": fg.version,
            "results": _query_fg(db, settings, fg, body.keys)}
```

`backend/app.py` 挂载(api_keys 下方;online 路由自带认证逻辑,不加全局 JWT 依赖——本项目各路由本就自带依赖,直接挂载即可):

```python
    from .routers import online as online_router

    app.include_router(online_router.router, prefix="/api")
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_online_api.py tests/ -v`
Expected: 新增 5 个 PASS,全量 152 passed

- [ ] **Step 5: Commit**

```bash
git add backend/routers/online.py backend/app.py tests/test_online_api.py
git commit -m "feat: 在线特征查询 API(API Key/TTL 过期标记/调用量/调试端点)"
```

---

### Task 5: 端到端(调度产出 → 物化 → 在线查询)与全量回归

**Files:**
- Create: `tests/test_e2e_online.py`

- [ ] **Step 1: 写端到端测试**

`tests/test_e2e_online.py`:

```python
"""端到端:特征组 → 工作流(duckdb 产 parquet → materialize)→ 触发 → 在线查询。"""
from tests.test_online_api import _login, _mk_key


def test_full_pipeline(client, admin_headers):
    client.post("/api/users", json={"username": "bob", "password": "bob123456",
                                    "role": "developer"}, headers=admin_headers)
    h = _login(client, "bob", "bob123456")
    pid = client.post("/api/projects", json={"name": "p1", "description": ""},
                      headers=h).json()["id"]
    h = {**h, "X-Project-Id": str(pid)}
    # 1. 工作流:t1 产 parquet(output_name=g 与特征组 offline_location 一致)→ t2 物化
    dag = {"nodes": [
        {"key": "t1", "type": "duckdb_sql",
         "params": {"sql": "select 'C1' as cust_no, '{{ ds }}' as dt, 42 as v",
                    "output_name": "g"}},
        {"key": "t2", "type": "materialize", "params": {}}],  # fg id 创建后回填
        "edges": [["t1", "t2"]]}
    wid = client.post("/api/workflows", json={
        "name": "wf", "description": "", "dag": dag, "cron": "0 2 * * *",
        "timezone": "Asia/Shanghai", "catchup": False, "concurrency_limit": 1,
        "failure_policy": "continue"}, headers=h).json()["id"]
    # 2. 特征组(绑定 t1)
    fgid = client.post("/api/feature-groups", json={
        "name": "g", "description": "", "entity_keys": ["cust_no"],
        "event_time_col": "dt", "ttl_days": 30, "online_enabled": True,
        "offline_kind": "parquet", "offline_location": "g",
        "workflow_id": wid, "task_key": "t1",
        "features": [{"name": "v", "dtype": "double", "description": "测试值"}],
        "upstream_tables": ["table:demo"]}, headers=h).json()["id"]
    # 3. 回填 materialize 节点参数(更新工作流 → 新版本)
    dag["nodes"][1]["params"] = {"feature_group_id": fgid}
    client.put(f"/api/workflows/{wid}", json={
        "name": "wf", "description": "", "dag": dag, "cron": "0 2 * * *",
        "timezone": "Asia/Shanghai", "catchup": False, "concurrency_limit": 1,
        "failure_policy": "continue"}, headers=h)
    # 4. 触发并驱动(sync 模式)
    rid = client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h).json()["id"]
    for _ in range(8):
        client.app.state.scheduler.advance_runs()
        client.app.state.executor.poll()
    detail = client.get(f"/api/runs/{rid}", headers=h).json()
    assert detail["state"] == "success", detail
    # 5. 生产即注册生效
    fg = client.get(f"/api/feature-groups/{fgid}", headers=h).json()
    assert fg["last_produced_rows"] == 1
    assert fg["materialize_watermark"] is not None
    # 6. 在线查询
    key = _mk_key(client, admin_headers)
    r = client.post("/api/online-features", headers={"X-API-Key": key},
                    json={"feature_group_id": fgid, "keys": [{"cust_no": "C1"}]})
    out = r.json()["results"][0]
    assert out["values"]["v"] == 42 and out["expired"] is False
```

- [ ] **Step 2: 运行确认通过**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/test_e2e_online.py -v`
Expected: PASS(1 passed;若 materialize 在 t2 执行时特征组查不到,检查 dag 回填后的版本是否生效——run 持有触发时的当前版本快照)

- [ ] **Step 3: 全量回归与冒烟**

Run: `D:/conda/envs/scpy310/python.exe -m pytest tests/ -v` → 153 passed
Run: `D:/conda/envs/scpy310/python.exe -c "from backend.app import create_app; app = create_app(); print('routes:', len(app.routes))"` → 正常
`git status --short` → 空

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e_online.py
git commit -m "test: 端到端——调度产出 Parquet→物化→在线查询全链路"
```

---

## Self-Review 记录

- **Spec 覆盖**:spec §7 在线特征服务(在线存储/物化水位/查询 API/API Key/TTL 标记/调用量/调试台后端)全部落地;五类任务插件至此全部实现。
- **占位符**:无。e2e 中 materialize 参数回填的版本快照注意事项已在 Step 2 说明。
- **类型一致性**:`online_store.upsert` 返回 (n, max_et) 与 materialize 使用一致;`build_entity_key` 在存储与查询两侧共用;`_connection_info` 复用自 sql_pushdown;event_time 口径(ISO 字符串)在计划头、online_store、materialize、online 路由四处一致。
