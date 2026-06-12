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
