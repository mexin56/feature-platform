import json

from sqlalchemy import select

from backend.models import TaskInstance, WorkflowRun
from tests.test_runs_api import _login, _mk_workspace


def _drive(client, n=6):
    for _ in range(n):
        client.app.state.scheduler.advance_runs()
        client.app.state.executor.poll()


def _fail_workflow(client, admin_headers):
    """构造必失败的工作流(SQL 查不存在的表,无重试)。"""
    h, pid, wid = _mk_workspace(client, admin_headers)
    dag = {"nodes": [{"key": "t1", "type": "duckdb_sql",
                      "params": {"sql": "select * from ghost"}}], "edges": []}
    wid2 = client.post("/api/workflows", json={
        "name": "failwf", "description": "", "dag": dag, "cron": None,
        "timezone": "Asia/Shanghai", "catchup": False, "concurrency_limit": 1,
        "failure_policy": "continue"}, headers=h).json()["id"]
    return h, pid, wid2


def test_stop_skips_pending(client, admin_headers):
    h, pid, wid = _mk_workspace(client, admin_headers)
    rid = client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h).json()["id"]
    r = client.post(f"/api/runs/{rid}/stop", headers=h)
    assert r.status_code == 200
    detail = client.get(f"/api/runs/{rid}", headers=h).json()
    assert detail["state"] == "stopped"
    assert detail["tasks"][0]["state"] == "skipped"


def test_retry_failed_run_from_failure_point(client, admin_headers):
    h, pid, wid2 = _fail_workflow(client, admin_headers)
    rid = client.post(f"/api/workflows/{wid2}/trigger", json={}, headers=h).json()["id"]
    _drive(client)
    assert client.get(f"/api/runs/{rid}", headers=h).json()["state"] == "failed"
    assert client.post(f"/api/runs/{rid}/retry", headers=h).status_code == 200
    detail = client.get(f"/api/runs/{rid}", headers=h).json()
    assert detail["state"] == "running"
    assert detail["tasks"][0]["state"] == "none"
    assert detail["tasks"][0]["try_number"] == 0  # 重试预算重置


def test_retry_running_run_rejected(client, admin_headers):
    h, pid, wid = _mk_workspace(client, admin_headers)
    rid = client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h).json()["id"]
    assert client.post(f"/api/runs/{rid}/retry", headers=h).status_code == 400


def test_mark_success_unblocks_downstream(client, admin_headers):
    h, pid, wid2 = _fail_workflow(client, admin_headers)
    rid = client.post(f"/api/workflows/{wid2}/trigger", json={}, headers=h).json()["id"]
    _drive(client)
    detail = client.get(f"/api/runs/{rid}", headers=h).json()
    tid = detail["tasks"][0]["id"]
    assert client.post(f"/api/tasks/{tid}/mark-success", headers=h).status_code == 200
    _drive(client, 3)
    detail = client.get(f"/api/runs/{rid}", headers=h).json()
    assert detail["tasks"][0]["state"] == "success"
    assert detail["state"] == "success"


def test_task_log_readable(client, admin_headers):
    h, pid, wid = _mk_workspace(client, admin_headers)
    rid = client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h).json()["id"]
    _drive(client)
    detail = client.get(f"/api/runs/{rid}", headers=h).json()
    tid = detail["tasks"][0]["id"]
    r = client.get(f"/api/tasks/{tid}/log", headers=h)
    assert r.status_code == 200
    assert "task_runner" in r.text


def test_audit_for_ops(client, admin_headers):
    h, pid, wid = _mk_workspace(client, admin_headers)
    rid = client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h).json()["id"]
    client.post(f"/api/runs/{rid}/stop", headers=h)
    actions = [a["action"] for a in
               client.get(f"/api/projects/{pid}/audit", headers=h).json()]
    assert "trigger_run" in actions and "stop_run" in actions


def test_retry_rejected_while_task_still_running(client, admin_headers):
    h, pid, wid = _mk_workspace(client, admin_headers)
    rid = client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h).json()["id"]
    client.post(f"/api/runs/{rid}/stop", headers=h)
    # 模拟:停止时仍有任务在运行(执行器尚未完成强杀)
    from backend.models import TaskInstance
    from sqlalchemy import select

    with client.app.state.sessionmaker() as db:
        ti = db.scalar(select(TaskInstance).where(TaskInstance.run_id == rid))
        ti.state = "running"
        db.commit()
    assert client.post(f"/api/runs/{rid}/retry", headers=h).status_code == 400
