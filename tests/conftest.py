import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings


@pytest.fixture()
def client(tmp_path):
    app = create_app(Settings(storage_dir=str(tmp_path), sync_scheduler=True))
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_headers(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}
