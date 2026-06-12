def test_create_returns_plaintext_once(client, admin_headers):
    r = client.post("/api/api-keys", json={"name": "risk-engine"}, headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body["key"]) >= 32  # 明文仅此一次
    lst = client.get("/api/api-keys", headers=admin_headers).json()
    assert lst[0]["name"] == "risk-engine"
    assert "key" not in lst[0] and "key_hash" not in lst[0]
    assert lst[0]["is_active"] is True and lst[0]["calls"] == 0


def test_duplicate_name_rejected(client, admin_headers):
    client.post("/api/api-keys", json={"name": "k"}, headers=admin_headers)
    assert client.post("/api/api-keys", json={"name": "k"},
                       headers=admin_headers).status_code == 400


def test_disable(client, admin_headers):
    kid = client.post("/api/api-keys", json={"name": "k"},
                      headers=admin_headers).json()["id"]
    assert client.post(f"/api/api-keys/{kid}/disable",
                       headers=admin_headers).status_code == 200
    lst = client.get("/api/api-keys", headers=admin_headers).json()
    assert lst[0]["is_active"] is False


def test_admin_only(client, admin_headers):
    client.post("/api/users", json={"username": "dev", "password": "dev123456",
                                    "role": "developer"}, headers=admin_headers)
    r = client.post("/api/auth/login", json={"username": "dev", "password": "dev123456"})
    dev = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.post("/api/api-keys", json={"name": "x"}, headers=dev).status_code == 403
    assert client.get("/api/api-keys", headers=dev).status_code == 403
