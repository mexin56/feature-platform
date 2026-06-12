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
