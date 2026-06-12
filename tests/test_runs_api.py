import json


def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_workspace(client, admin_headers):
    """developer + 项目 + 含 duckdb 节点的工作流,返回 (headers, pid, wid)。"""
    client.post("/api/users", json={"username": "bob", "password": "bob123456",
                                    "role": "developer"}, headers=admin_headers)
    h = _login(client, "bob", "bob123456")
    pid = client.post("/api/projects", json={"name": "p1", "description": ""},
                      headers=h).json()["id"]
    h = {**h, "X-Project-Id": str(pid)}
    dag = {"nodes": [{"key": "t1", "type": "duckdb_sql",
                      "params": {"sql": "select '{{ ds }}' as d"}}], "edges": []}
    wid = client.post("/api/workflows", json={
        "name": "wf", "description": "", "dag": dag, "cron": "0 2 * * *",
        "timezone": "Asia/Shanghai", "catchup": False, "concurrency_limit": 1,
        "failure_policy": "continue"}, headers=h).json()["id"]
    return h, pid, wid


def _drive(client, n=6):
    """sync 模式驱动调度与执行。"""
    for _ in range(n):
        client.app.state.scheduler.advance_runs()
        client.app.state.executor.poll()


def test_trigger_default_interval_and_execute(client, admin_headers):
    h, pid, wid = _mk_workspace(client, admin_headers)
    r = client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h)
    assert r.status_code == 200
    rid = r.json()["id"]
    assert r.json()["run_type"] == "manual"
    _drive(client)
    detail = client.get(f"/api/runs/{rid}", headers=h).json()
    assert detail["state"] == "success"
    assert detail["tasks"][0]["state"] == "success"
    assert json.loads(detail["tasks"][0]["result_json"])["rows"] == 1


def test_trigger_explicit_interval(client, admin_headers):
    h, pid, wid = _mk_workspace(client, admin_headers)
    r = client.post(f"/api/workflows/{wid}/trigger", json={
        "data_interval_start": "2026-06-01T00:00:00",
        "data_interval_end": "2026-06-02T00:00:00"}, headers=h)
    assert r.json()["data_interval_start"] == "2026-06-01T00:00:00"


def test_backfill_creates_interval_runs(client, admin_headers):
    h, pid, wid = _mk_workspace(client, admin_headers)
    r = client.post(f"/api/workflows/{wid}/backfill", json={
        "start_date": "2026-06-01T00:00:00", "end_date": "2026-06-04T00:00:00",
        "parallel": 2}, headers=h)
    assert r.status_code == 200
    # Derivation: cron "0 2 * * *", start=2026-06-01 00:00, end=2026-06-04 00:00
    # Iterator starts at start_date - 1us ≈ 2026-05-31 23:59:59.999999
    # a = get_next() → 2026-06-01 02:00
    # loop b = get_next():
    #   b=2026-06-02 02:00 ≤ end(2026-06-04 00:00) → create (06-01 02:00 → 06-02 02:00), created=1
    #   b=2026-06-03 02:00 ≤ end → create (06-02 02:00 → 06-03 02:00), created=2
    #   b=2026-06-04 02:00 > end → break
    # Total: 2 runs created
    assert r.json()["created"] == 2
    runs = client.get(f"/api/workflows/{wid}/runs", headers=h).json()
    assert all(x["run_type"] == "backfill" for x in runs)
    assert all(x["parallel_degree"] == 2 for x in runs)


def test_backfill_requires_cron(client, admin_headers):
    h, pid, wid = _mk_workspace(client, admin_headers)
    dag = {"nodes": [{"key": "t1", "type": "duckdb_sql", "params": {"sql": "select 1"}}],
           "edges": []}
    wid2 = client.post("/api/workflows", json={
        "name": "nocron", "description": "", "dag": dag, "cron": None,
        "timezone": "Asia/Shanghai", "catchup": False, "concurrency_limit": 1,
        "failure_policy": "continue"}, headers=h).json()["id"]
    r = client.post(f"/api/workflows/{wid2}/backfill", json={
        "start_date": "2026-06-01T00:00:00", "end_date": "2026-06-02T00:00:00"}, headers=h)
    assert r.status_code == 400


def test_runs_listing_and_isolation(client, admin_headers):
    h, pid, wid = _mk_workspace(client, admin_headers)
    client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h)
    lst = client.get(f"/api/workflows/{wid}/runs", headers=h).json()
    assert len(lst) == 1
    # 其他项目成员不可见
    client.post("/api/users", json={"username": "eve", "password": "eve123456",
                                    "role": "developer"}, headers=admin_headers)
    eh = _login(client, "eve", "eve123456")
    pid2 = client.post("/api/projects", json={"name": "p2", "description": ""},
                       headers=eh).json()["id"]
    eh = {**eh, "X-Project-Id": str(pid2)}
    assert client.get(f"/api/workflows/{wid}/runs", headers=eh).status_code == 404
    rid = lst[0]["id"]
    assert client.get(f"/api/runs/{rid}", headers=eh).status_code == 404
