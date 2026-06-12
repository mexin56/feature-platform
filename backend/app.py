from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from .config import Settings
from .db import Base, make_engine


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_dirs()
    engine = make_engine(settings.db_path)

    app = FastAPI(title="特征调度管理平台")
    app.state.settings = settings
    app.state.sessionmaker = sessionmaker(bind=engine)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    from .routers import auth as auth_router

    app.include_router(auth_router.router, prefix="/api")

    from . import models  # noqa: F401  确保模型注册

    Base.metadata.create_all(engine)
    _seed_admin(app.state.sessionmaker)
    return app


def _seed_admin(SessionLocal) -> None:
    """首次启动播种默认管理员 admin/admin123(请登录后立即改密)。"""
    from sqlalchemy import func, select

    from .models import User
    from .services.auth import hash_password

    with SessionLocal() as db:
        if (db.scalar(select(func.count(User.id))) or 0) == 0:
            db.add(User(username="admin", password_hash=hash_password("admin123"), role="admin"))
            db.commit()
