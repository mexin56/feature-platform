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
