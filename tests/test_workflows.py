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
