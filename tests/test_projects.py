def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_user(client, admin_headers, name, role="developer"):
    client.post("/api/users", json={"username": name, "password": name + "123456", "role": role}, headers=admin_headers)
    return _login(client, name, name + "123456")


def test_create_project_owner_is_member(client, admin_headers):
    bob = _mk_user(client, admin_headers, "bob")
    r = client.post("/api/projects", json={"name": "反欺诈特征", "description": "d"}, headers=bob)
    assert r.status_code == 200
    pid = r.json()["id"]
    mine = client.get("/api/projects", headers=bob).json()
    assert [p["id"] for p in mine] == [pid]


def test_project_isolation(client, admin_headers):
    bob = _mk_user(client, admin_headers, "bob")
    eve = _mk_user(client, admin_headers, "eve")
    client.post("/api/projects", json={"name": "p1", "description": ""}, headers=bob)
    assert client.get("/api/projects", headers=eve).json() == []   # 非成员不可见
    assert len(client.get("/api/projects", headers=admin_headers).json()) == 1  # admin 全见


def test_member_management(client, admin_headers):
    bob = _mk_user(client, admin_headers, "bob")
    eve = _mk_user(client, admin_headers, "eve")
    pid = client.post("/api/projects", json={"name": "p1", "description": ""}, headers=bob).json()["id"]
    eve_id = next(u["id"] for u in client.get("/api/users", headers=admin_headers).json() if u["username"] == "eve")
    # bob(owner)加 eve 为成员
    assert client.post(f"/api/projects/{pid}/members", json={"user_id": eve_id}, headers=bob).status_code == 200
    assert len(client.get("/api/projects", headers=eve).json()) == 1
    # eve(非 owner)不能管理成员
    bob_id = next(u["id"] for u in client.get("/api/users", headers=admin_headers).json() if u["username"] == "bob")
    assert client.delete(f"/api/projects/{pid}/members/{bob_id}", headers=eve).status_code == 403
    # bob 移除 eve
    assert client.delete(f"/api/projects/{pid}/members/{eve_id}", headers=bob).status_code == 200
    assert client.get("/api/projects", headers=eve).json() == []


def test_audit_logged(client, admin_headers):
    bob = _mk_user(client, admin_headers, "bob")
    pid = client.post("/api/projects", json={"name": "p1", "description": ""}, headers=bob).json()["id"]
    logs = client.get(f"/api/projects/{pid}/audit", headers=bob).json()
    assert logs[0]["action"] == "create_project"
    assert logs[0]["username"] == "bob"
