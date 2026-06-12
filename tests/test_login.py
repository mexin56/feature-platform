def test_login_ok(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    body = r.json()
    assert body["token"]
    assert body["user"]["username"] == "admin"
    assert body["user"]["role"] == "admin"


def test_login_wrong_password(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_me_requires_token(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_ok(client, admin_headers):
    r = client.get("/api/auth/me", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["username"] == "admin"


def test_change_password(client, admin_headers):
    r = client.post(
        "/api/auth/change-password",
        json={"old_password": "admin123", "new_password": "newpass1"},
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert client.post(
        "/api/auth/login", json={"username": "admin", "password": "newpass1"}
    ).status_code == 200
    assert client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    ).status_code == 401
