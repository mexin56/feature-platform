def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_admin_create_list_user(client, admin_headers):
    r = client.post(
        "/api/users",
        json={"username": "bob", "password": "bob123456", "role": "developer"},
        headers=admin_headers,
    )
    assert r.status_code == 200
    names = [u["username"] for u in client.get("/api/users", headers=admin_headers).json()]
    assert "bob" in names


def test_duplicate_username_rejected(client, admin_headers):
    client.post("/api/users", json={"username": "bob", "password": "bob123456", "role": "developer"}, headers=admin_headers)
    r = client.post("/api/users", json={"username": "bob", "password": "x2345678", "role": "viewer"}, headers=admin_headers)
    assert r.status_code == 400


def test_non_admin_cannot_manage_users(client, admin_headers):
    client.post("/api/users", json={"username": "bob", "password": "bob123456", "role": "developer"}, headers=admin_headers)
    bob = _login(client, "bob", "bob123456")
    assert client.post("/api/users", json={"username": "eve", "password": "e1234567", "role": "viewer"}, headers=bob).status_code == 403


def test_disable_and_reset(client, admin_headers):
    uid = client.post(
        "/api/users", json={"username": "bob", "password": "bob123456", "role": "developer"}, headers=admin_headers
    ).json()["id"]
    # 重置密码
    assert client.post(f"/api/users/{uid}/reset-password", json={"new_password": "reset123"}, headers=admin_headers).status_code == 200
    assert client.post("/api/auth/login", json={"username": "bob", "password": "reset123"}).status_code == 200
    # 禁用后无法登录
    assert client.patch(f"/api/users/{uid}", json={"is_active": False}, headers=admin_headers).status_code == 200
    assert client.post("/api/auth/login", json={"username": "bob", "password": "reset123"}).status_code == 401


def test_viewer_readonly_enforced(client, admin_headers):
    client.post("/api/users", json={"username": "ro", "password": "ro123456", "role": "viewer"}, headers=admin_headers)
    ro = _login(client, "ro", "ro123456")
    # viewer 可 GET
    assert client.get("/api/auth/me", headers=ro).status_code == 200
    # viewer 任何写操作被拒(403 在 get_current_user 层)
    assert client.post("/api/auth/change-password", json={"old_password": "ro123456", "new_password": "x1234567"}, headers=ro).status_code == 403


def test_admin_cannot_demote_self(client, admin_headers):
    admin_id = next(u["id"] for u in client.get("/api/users", headers=admin_headers).json() if u["username"] == "admin")
    r = client.patch(f"/api/users/{admin_id}", json={"role": "developer"}, headers=admin_headers)
    assert r.status_code == 400
