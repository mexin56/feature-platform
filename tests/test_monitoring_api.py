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
    # dashboard 只读:lag_hours 正确显示,不产生告警
    d = client.get("/api/monitoring/dashboard", headers=h).json()
    lag = [x for x in d["feature_groups"] if x["id"] == fg["id"]][0]
    assert lag["lag_hours"] >= 71
    # 告警由调度器产生(调度化)
    client.app.state.scheduler.check_materialize_lag()
    alerts = client.get("/api/alerts", headers=h).json()
    assert any(a["kind"] == "materialize_lag" for a in alerts)
    # 当天去重:重置节流后再次调用,仍只有 1 条
    client.app.state.scheduler._last_lag_check = None
    client.app.state.scheduler.check_materialize_lag()
    alerts2 = client.get("/api/alerts", headers=h).json()
    assert len([a for a in alerts2 if a["kind"] == "materialize_lag"]) == 1
