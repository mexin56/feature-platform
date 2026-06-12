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


def test_batch_size_capped(client, admin_headers):
    h, fgid = _mk_fg(client, admin_headers)
    key = _mk_key(client, admin_headers)
    r = client.post("/api/online-features", headers={"X-API-Key": key},
                    json={"feature_group_id": fgid,
                          "keys": [{"cust_no": str(i)} for i in range(501)]})
    assert r.status_code == 422  # Pydantic 校验拒绝,防 SQLite 999 参数上限
