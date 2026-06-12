from backend.app import create_app
from backend.config import Settings


def test_sync_mode_exposes_scheduler_and_executor(tmp_path):
    app = create_app(Settings(storage_dir=str(tmp_path), sync_scheduler=True))
    assert app.state.scheduler is not None
    assert app.state.executor is not None
    assert app.state.executor.sync is True
    assert getattr(app.state, "scheduler_thread", None) is None  # sync 不起线程


def test_async_mode_starts_thread_on_startup(tmp_path):
    from fastapi.testclient import TestClient

    app = create_app(Settings(storage_dir=str(tmp_path), sync_scheduler=False))
    with TestClient(app):  # 触发 startup/shutdown 事件
        import time

        time.sleep(0.2)
        assert app.state.scheduler_thread is not None
        assert app.state.scheduler_thread.is_alive()
    assert app.state.scheduler_stop.is_set()  # shutdown 后已请求停止
