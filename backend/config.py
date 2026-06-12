import os
from pathlib import Path


class Settings:
    """运行时配置。storage_dir 可注入,测试用 tmp_path 隔离。"""

    def __init__(self, storage_dir: str | None = None, sync_scheduler: bool = False):
        base = Path(__file__).resolve().parent.parent
        self.storage_dir = Path(
            storage_dir or os.environ.get("FEATURE_PLATFORM_STORAGE", base / "storage")
        )
        self.offline_dir = self.storage_dir / "offline"
        self.logs_dir = self.storage_dir / "logs"
        self.scripts_dir = self.storage_dir / "scripts"
        self.db_path = self.storage_dir / "meta.db"
        self.online_db_path = self.storage_dir / "online_store.db"
        # 测试模式:不启动 tick 线程,由测试手动驱动调度循环(Phase 1b 使用)
        self.sync_scheduler = sync_scheduler

    def ensure_dirs(self) -> None:
        for d in (self.offline_dir, self.logs_dir, self.scripts_dir):
            d.mkdir(parents=True, exist_ok=True)
