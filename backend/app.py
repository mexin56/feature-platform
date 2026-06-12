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

    from .routers import users as users_router

    app.include_router(users_router.router, prefix="/api")

    from .routers import projects as projects_router

    app.include_router(projects_router.router, prefix="/api")

    from .routers import connections as connections_router

    app.include_router(connections_router.router, prefix="/api")

    from .routers import workflows as workflows_router

    app.include_router(workflows_router.router, prefix="/api")

    from .routers import runs as runs_router

    app.include_router(runs_router.router, prefix="/api")

    from .routers import feature_groups as feature_groups_router

    app.include_router(feature_groups_router.router, prefix="/api")

    from . import models  # noqa: F401  确保模型注册

    Base.metadata.create_all(engine)
    from .db import ensure_column

    ensure_column(engine, "workflow_runs", "parallel_degree", "INTEGER DEFAULT 1")
    _seed_admin(app.state.sessionmaker)

    from .services.executor import Executor
    from .services.scheduler import Scheduler

    app.state.scheduler = Scheduler(app.state.sessionmaker, settings)
    app.state.executor = Executor(app.state.sessionmaker, settings,
                                  max_workers=settings.max_workers,
                                  sync=settings.sync_scheduler)
    app.state.scheduler_thread = None
    if not settings.sync_scheduler:
        import threading

        stop = threading.Event()
        app.state.scheduler_stop = stop

        def _loop():
            app.state.scheduler.reap_orphans()  # 启动期孤儿清理
            while not stop.wait(settings.tick_interval_sec):
                try:
                    app.state.scheduler.tick()
                    app.state.executor.poll()
                except Exception:  # noqa: BLE001  调度循环永不退出
                    import traceback

                    traceback.print_exc()

        @app.on_event("startup")
        def _start_scheduler():
            if app.state.scheduler_thread is not None and app.state.scheduler_thread.is_alive():
                return  # 已在运行,防止重复启动(如 TestClient 多次进入)
            t = threading.Thread(target=_loop, daemon=True, name="scheduler")
            app.state.scheduler_thread = t
            t.start()

        @app.on_event("shutdown")
        def _stop_scheduler():
            stop.set()

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
