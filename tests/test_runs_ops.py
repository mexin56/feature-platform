import json

from sqlalchemy import select

from backend.models import TaskInstance, WorkflowRun
from tests.test_runs_api import _login, _mk_workspace


# ---------------------------------------------------------------------------
# 辅助:构造第二个工作流(同项目)
# ---------------------------------------------------------------------------

def _mk_second_workflow(client, h, name="wf2"):
    """在已有 headers+project 下创建第二个工作流,返回 wid2。"""
    dag = {"nodes": [{"key": "t2", "type": "duckdb_sql",
                      "params": {"sql": "select 2 as n"}}], "edges": []}
    r = client.post("/api/workflows", json={
        "name": name, "description": "", "dag": dag, "cron": None,
        "timezone": "Asia/Shanghai", "catchup": False, "concurrency_limit": 1,
        "failure_policy": "continue"}, headers=h)
    return r.json()["id"]


def _mk_viewer(client, admin_headers, pid):
    """创建 viewer 用户并加入项目,返回带 X-Project-Id 的 headers。"""
    r = client.post("/api/users", json={"username": "viewer1", "password": "viewer1234",
                                        "role": "viewer"}, headers=admin_headers)
    viewer_id = r.json()["id"]
    vh = _login(client, "viewer1", "viewer1234")
    # admin 把 viewer 加入项目(MemberIn 接收 user_id)
    client.post(f"/api/projects/{pid}/members",
                json={"user_id": viewer_id}, headers=admin_headers)
    return {**vh, "X-Project-Id": str(pid)}


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


# ===========================================================================
# GET /api/runs — 跨工作流实例列表
# ===========================================================================

def test_list_all_runs_cross_workflow(client, admin_headers):
    """两个工作流各触发一个实例,GET /api/runs 返回两条(最新优先),并携带 workflow_name。"""
    h, pid, wid = _mk_workspace(client, admin_headers)
    wid2 = _mk_second_workflow(client, h)
    rid1 = client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h).json()["id"]
    rid2 = client.post(f"/api/workflows/{wid2}/trigger", json={}, headers=h).json()["id"]

    r = client.get("/api/runs", headers=h)
    assert r.status_code == 200
    items = r.json()
    ids = [x["id"] for x in items]
    # 最新优先:rid2 > rid1
    assert ids.index(rid2) < ids.index(rid1)
    # 都带 workflow_name 字段
    for item in items:
        assert "workflow_name" in item
        assert item["workflow_name"] != ""
    # 字段形状完整
    for key in ("id", "workflow_id", "run_type", "state", "parallel_degree",
                "data_interval_start", "data_interval_end", "created_at",
                "finished_at", "workflow_name"):
        assert key in items[0]


def test_list_all_runs_filter_by_workflow_id(client, admin_headers):
    h, pid, wid = _mk_workspace(client, admin_headers)
    wid2 = _mk_second_workflow(client, h)
    client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h)
    client.post(f"/api/workflows/{wid2}/trigger", json={}, headers=h)

    r = client.get(f"/api/runs?workflow_id={wid}", headers=h)
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["workflow_id"] == wid


def test_list_all_runs_filter_by_state(client, admin_headers):
    h, pid, wid = _mk_workspace(client, admin_headers)
    # 触发后立即停止一个,另一个保持 running
    rid1 = client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h).json()["id"]
    client.post(f"/api/runs/{rid1}/stop", headers=h)
    client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h)

    r = client.get("/api/runs?state=stopped", headers=h)
    items = r.json()
    assert all(x["state"] == "stopped" for x in items)
    assert len(items) == 1


def test_list_all_runs_filter_by_run_type(client, admin_headers):
    h, pid, wid = _mk_workspace(client, admin_headers)
    # manual 触发一个,backfill 两个
    client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h)
    client.post(f"/api/workflows/{wid}/backfill", json={
        "start_date": "2026-06-01T00:00:00", "end_date": "2026-06-04T00:00:00"}, headers=h)

    r = client.get("/api/runs?run_type=backfill", headers=h)
    items = r.json()
    assert len(items) == 2
    assert all(x["run_type"] == "backfill" for x in items)

    r2 = client.get("/api/runs?run_type=manual", headers=h)
    items2 = r2.json()
    assert len(items2) == 1
    assert items2[0]["run_type"] == "manual"


def test_list_all_runs_excludes_other_project(client, admin_headers):
    """GET /api/runs 不能看到其他项目的实例。"""
    h, pid, wid = _mk_workspace(client, admin_headers)
    client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h)

    # 创建第二个项目+工作流
    client.post("/api/users", json={"username": "carol", "password": "carol123",
                                    "role": "developer"}, headers=admin_headers)
    ch = _login(client, "carol", "carol123")
    pid2 = client.post("/api/projects", json={"name": "carol_proj", "description": ""},
                       headers=ch).json()["id"]
    ch = {**ch, "X-Project-Id": str(pid2)}
    dag = {"nodes": [{"key": "t1", "type": "duckdb_sql", "params": {"sql": "select 1"}}],
           "edges": []}
    wid_carol = client.post("/api/workflows", json={
        "name": "carol_wf", "description": "", "dag": dag, "cron": None,
        "timezone": "Asia/Shanghai", "catchup": False, "concurrency_limit": 1,
        "failure_policy": "continue"}, headers=ch).json()["id"]
    client.post(f"/api/workflows/{wid_carol}/trigger", json={}, headers=ch)

    # carol 只看到自己项目的 1 个 run
    carol_runs = client.get("/api/runs", headers=ch).json()
    assert all(item["workflow_id"] == wid_carol for item in carol_runs)
    assert len(carol_runs) == 1

    # bob 只看到自己项目的 1 个 run
    bob_runs = client.get("/api/runs", headers=h).json()
    assert all(item["workflow_id"] == wid for item in bob_runs)
    assert len(bob_runs) == 1


def test_list_all_runs_viewer_can_get(client, admin_headers):
    """viewer 可以读 GET /api/runs。"""
    h, pid, wid = _mk_workspace(client, admin_headers)
    client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h)

    vh = _mk_viewer(client, admin_headers, pid)
    r = client.get("/api/runs", headers=vh)
    assert r.status_code == 200
    assert len(r.json()) == 1


# ===========================================================================
# POST /api/runs/{rid}/mark-success — 实例级强制成功
# ===========================================================================

def _mk_failed_run(client, admin_headers):
    """构造并驱动到 failed 状态的实例,返回 (h, pid, rid)。"""
    h, pid, wid = _mk_workspace(client, admin_headers)
    dag = {"nodes": [{"key": "bad", "type": "duckdb_sql",
                      "params": {"sql": "select * from ghost_table"}}], "edges": []}
    wid2 = client.post("/api/workflows", json={
        "name": "failwf2", "description": "", "dag": dag, "cron": None,
        "timezone": "Asia/Shanghai", "catchup": False, "concurrency_limit": 1,
        "failure_policy": "continue"}, headers=h).json()["id"]
    rid = client.post(f"/api/workflows/{wid2}/trigger", json={}, headers=h).json()["id"]
    _drive(client)
    assert client.get(f"/api/runs/{rid}", headers=h).json()["state"] == "failed"
    return h, pid, rid


def test_mark_success_run_failed(client, admin_headers):
    """对失败实例调用强制成功:所有任务变 success,实例变 success。"""
    h, pid, rid = _mk_failed_run(client, admin_headers)
    r = client.post(f"/api/runs/{rid}/mark-success", headers=h)
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    detail = client.get(f"/api/runs/{rid}", headers=h).json()
    assert detail["state"] == "success"
    assert detail["finished_at"] is not None
    assert all(t["state"] == "success" for t in detail["tasks"])


def test_mark_success_run_mixed_task_states(client, admin_headers):
    """停止后手动设置混合任务状态,强制成功应把 non-success 全部置 success。"""
    h, pid, wid = _mk_workspace(client, admin_headers)
    rid = client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h).json()["id"]
    # 停止实例
    client.post(f"/api/runs/{rid}/stop", headers=h)

    # 手动制造混合状态:t1 → failed, 并添加一个 skipped 任务记录
    with client.app.state.sessionmaker() as db:
        ti = db.scalar(select(TaskInstance).where(TaskInstance.run_id == rid))
        ti.state = "failed"
        db.commit()

    r = client.post(f"/api/runs/{rid}/mark-success", headers=h)
    assert r.status_code == 200

    detail = client.get(f"/api/runs/{rid}", headers=h).json()
    assert detail["state"] == "success"
    assert all(t["state"] == "success" for t in detail["tasks"])


def test_mark_success_run_already_success_400(client, admin_headers):
    """实例已成功时返回 400。"""
    h, pid, wid = _mk_workspace(client, admin_headers)
    rid = client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h).json()["id"]
    _drive(client)
    assert client.get(f"/api/runs/{rid}", headers=h).json()["state"] == "success"

    r = client.post(f"/api/runs/{rid}/mark-success", headers=h)
    assert r.status_code == 400
    assert "实例已成功" in r.json()["detail"]


def test_mark_success_run_blocked_when_task_running(client, admin_headers):
    """有 running 任务时返回 400。"""
    h, pid, wid = _mk_workspace(client, admin_headers)
    rid = client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h).json()["id"]
    # run 默认状态 running,任务默认 none;手动设一个任务为 running
    with client.app.state.sessionmaker() as db:
        ti = db.scalar(select(TaskInstance).where(TaskInstance.run_id == rid))
        ti.state = "running"
        db.commit()

    r = client.post(f"/api/runs/{rid}/mark-success", headers=h)
    assert r.status_code == 400
    assert "仍在运行" in r.json()["detail"]


def test_mark_success_run_viewer_403(client, admin_headers):
    """viewer 调用 POST mark-success 返回 403。"""
    h, pid, wid = _mk_workspace(client, admin_headers)
    rid = client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h).json()["id"]
    client.post(f"/api/runs/{rid}/stop", headers=h)

    vh = _mk_viewer(client, admin_headers, pid)
    r = client.post(f"/api/runs/{rid}/mark-success", headers=vh)
    assert r.status_code == 403


def test_mark_success_run_cross_project_404(client, admin_headers):
    """其他项目成员调用 POST mark-success 返回 404。"""
    h, pid, wid = _mk_workspace(client, admin_headers)
    rid = client.post(f"/api/workflows/{wid}/trigger", json={}, headers=h).json()["id"]
    client.post(f"/api/runs/{rid}/stop", headers=h)

    # 创建另一个项目成员
    client.post("/api/users", json={"username": "dave", "password": "dave1234",
                                    "role": "developer"}, headers=admin_headers)
    dh = _login(client, "dave", "dave1234")
    pid2 = client.post("/api/projects", json={"name": "dave_proj", "description": ""},
                       headers=dh).json()["id"]
    dh = {**dh, "X-Project-Id": str(pid2)}

    r = client.post(f"/api/runs/{rid}/mark-success", headers=dh)
    assert r.status_code == 404


def test_mark_success_run_audit_recorded(client, admin_headers):
    """mark_success_run 操作落审计日志。"""
    h, pid, rid = _mk_failed_run(client, admin_headers)
    client.post(f"/api/runs/{rid}/mark-success", headers=h)
    actions = [a["action"] for a in
               client.get(f"/api/projects/{pid}/audit", headers=h).json()]
    assert "mark_success_run" in actions
