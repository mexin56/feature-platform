import duckdb


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


def _mk_fg_with_parquet(client, h, rows_sql):
    """建 parquet 特征组并写入快照文件。"""
    fg = client.post("/api/feature-groups", json={
        "name": "demo_fg", "description": "", "entity_keys": ["cust_no"],
        "event_time_col": None, "ttl_days": None, "online_enabled": False,
        "offline_kind": "parquet", "offline_location": "demo_fg",
        "features": [{"name": "v", "dtype": "int", "description": ""}],
        "upstream_tables": []}, headers=h).json()
    out = client.app.state.settings.offline_dir / "demo_fg"
    out.mkdir(parents=True, exist_ok=True)
    duckdb.sql(f"COPY ({rows_sql}) TO '{(out / 'p.parquet').as_posix()}' (FORMAT PARQUET)")
    return fg["id"]


def test_duckdb_query_feature_view(client, admin_headers):
    h, pid = _mk_ws(client, admin_headers)
    _mk_fg_with_parquet(client, h, "select 'C1' cust_no, 1 v union all select 'C2', 2")
    r = client.post("/api/query", json={
        "engine": "duckdb", "sql": "select cust_no, v from demo_fg order by cust_no"},
        headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["columns"] == ["cust_no", "v"]
    assert body["rows"] == [["C1", 1], ["C2", 2]]
    assert "demo_fg" in body["views"]
    assert body["truncated"] is False
    assert body["elapsed_ms"] >= 0


def test_query_guard_readonly_and_single_statement(client, admin_headers):
    h, _ = _mk_ws(client, admin_headers)
    r = client.post("/api/query", json={"engine": "duckdb", "sql": "drop table x"}, headers=h)
    assert r.status_code == 400 and "只读" in r.json()["detail"]
    r = client.post("/api/query", json={"engine": "duckdb",
                                        "sql": "select 1; select 2"}, headers=h)
    assert r.status_code == 400 and "单条" in r.json()["detail"]
    r = client.post("/api/query", json={"engine": "duckdb", "sql": "  "}, headers=h)
    assert r.status_code == 400


def test_query_limit_truncates(client, admin_headers):
    h, _ = _mk_ws(client, admin_headers)
    _mk_fg_with_parquet(client, h,
                        "select 'C1' cust_no, 1 v union all select 'C2', 2 "
                        "union all select 'C3', 3")
    r = client.post("/api/query", json={
        "engine": "duckdb", "sql": "select * from demo_fg", "limit": 2}, headers=h)
    body = r.json()
    assert body["row_count"] == 2 and body["truncated"] is True


def test_query_connection_mocked(client, admin_headers, monkeypatch):
    h, _ = _mk_ws(client, admin_headers)
    cid = client.post("/api/connections", json={
        "name": "dw", "conn_type": "mysql", "host": "h", "port": 3306,
        "username": "u", "password": "pw", "database": "dw"},
        headers=admin_headers).json()["id"]
    captured = {}

    def fake_fetch(info, sql):
        captured["sql"] = sql
        return ["a"], [(1,), (2,)]

    from backend.services.plugins import materialize

    monkeypatch.setattr(materialize, "_fetch_rows", fake_fetch)
    r = client.post("/api/query", json={
        "engine": "connection", "connection_id": cid,
        "sql": "select a from t", "limit": 100}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [[1], [2]]
    assert "limit 100" in captured["sql"]  # SELECT 语句下推时已包行数限制


def test_query_connection_requires_id(client, admin_headers):
    h, _ = _mk_ws(client, admin_headers)
    r = client.post("/api/query", json={"engine": "connection", "sql": "select 1"},
                    headers=h)
    assert r.status_code == 400


def test_query_viewer_blocked(client, admin_headers):
    h, pid = _mk_ws(client, admin_headers)
    client.post("/api/users", json={"username": "ro", "password": "ro123456",
                                    "role": "viewer"}, headers=admin_headers)
    ro = {**_login(client, "ro", "ro123456"), "X-Project-Id": str(pid)}
    r = client.post("/api/query", json={"engine": "duckdb", "sql": "select 1"}, headers=ro)
    assert r.status_code == 403
