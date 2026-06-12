def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_dev(client, admin_headers):
    client.post("/api/users", json={"username": "dev", "password": "dev123456", "role": "developer"}, headers=admin_headers)
    return _login(client, "dev", "dev123456")


CONN = {"name": "数仓", "conn_type": "spark", "host": "10.0.0.1", "port": 10000,
        "username": "hive", "password": "secret", "database": "dw"}


def test_create_and_list_masks_password(client, admin_headers):
    r = client.post("/api/connections", json=CONN, headers=admin_headers)
    assert r.status_code == 200
    out = client.get("/api/connections", headers=admin_headers).json()
    assert out[0]["has_password"] is True
    assert "password" not in out[0] and "password_enc" not in out[0]


def test_password_encrypted_in_db(client, admin_headers):
    client.post("/api/connections", json=CONN, headers=admin_headers)
    app = client.app
    from sqlalchemy import select

    from backend.models import Connection

    with app.state.sessionmaker() as db:
        c = db.scalar(select(Connection))
    assert c.password_enc != "secret" and len(c.password_enc) > 20


def test_developer_can_list_but_not_manage(client, admin_headers):
    client.post("/api/connections", json=CONN, headers=admin_headers)
    dev = _mk_dev(client, admin_headers)
    assert client.get("/api/connections", headers=dev).status_code == 200
    assert client.post("/api/connections", json={**CONN, "name": "x"}, headers=dev).status_code == 403
    assert client.delete("/api/connections/1", headers=dev).status_code == 403


def test_invalid_type_rejected(client, admin_headers):
    r = client.post("/api/connections", json={**CONN, "conn_type": "oracle"}, headers=admin_headers)
    assert r.status_code == 400


def test_patch_keeps_password_when_omitted(client, admin_headers):
    cid = client.post("/api/connections", json=CONN, headers=admin_headers).json()["id"]
    assert client.patch(f"/api/connections/{cid}", json={"host": "10.0.0.2"}, headers=admin_headers).status_code == 200
    from sqlalchemy import select

    from backend.models import Connection

    with client.app.state.sessionmaker() as db:
        c = db.scalar(select(Connection))
    assert c.host == "10.0.0.2"
    assert c.password_enc  # 未传 password 不清空


def test_test_endpoint_uses_connector(client, admin_headers, monkeypatch):
    cid = client.post("/api/connections", json=CONN, headers=admin_headers).json()["id"]
    calls = {}

    def fake_test(conn_type, host, port, username, password, database):
        calls.update(dict(conn_type=conn_type, password=password))

    from backend.services import connectors

    monkeypatch.setattr(connectors, "test_connection", fake_test)
    r = client.post(f"/api/connections/{cid}/test", headers=admin_headers)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert calls["password"] == "secret"  # 解密后传给连接器

    def fail_test(*a, **kw):
        raise RuntimeError("连接超时")

    monkeypatch.setattr(connectors, "test_connection", fail_test)
    assert client.post(f"/api/connections/{cid}/test", headers=admin_headers).status_code == 400


def test_delete(client, admin_headers):
    cid = client.post("/api/connections", json=CONN, headers=admin_headers).json()["id"]
    assert client.delete(f"/api/connections/{cid}", headers=admin_headers).status_code == 200
    assert client.get("/api/connections", headers=admin_headers).json() == []
