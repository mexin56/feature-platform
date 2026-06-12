from sqlalchemy import select

from backend.app import create_app
from backend.config import Settings
from backend.models import User


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_seed_admin_once(tmp_path):
    """首次启动播种 admin/admin123;重复启动不重复播种。"""
    settings = Settings(storage_dir=str(tmp_path), sync_scheduler=True)
    app = create_app(settings)
    app2 = create_app(settings)  # 第二次启动
    with app.state.sessionmaker() as db:
        users = db.scalars(select(User)).all()
    assert len(users) == 1
    assert users[0].username == "admin"
    assert users[0].role == "admin"
