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
        self.market_db = self.storage_dir / "market.duckdb"

        # PostgreSQL 连接串(集市写入+只读查询)
        pg_user = os.environ.get("FP_PG_USER", "quantdinger")
        pg_pass = os.environ.get("FP_PG_PASSWORD", "quantdinger123")
        pg_host = os.environ.get("FP_PG_HOST", "localhost")
        pg_port = os.environ.get("FP_PG_PORT", "5432")
        pg_db = os.environ.get("FP_PG_DB", "feature_platform")
        self.pg_url = f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"

        # 集市写入引擎: duckdb / postgres
        self.market_engine = os.environ.get("FP_MARKET_ENGINE", "postgres")

        # 测试模式:不启动 tick 线程,由测试手动驱动调度循环
        self.sync_scheduler = sync_scheduler
        self.max_workers = int(os.environ.get("FEATURE_PLATFORM_MAX_WORKERS", "4"))
        self.tick_interval_sec = 5

    def ensure_dirs(self) -> None:
        for d in (self.offline_dir, self.logs_dir, self.scripts_dir):
            d.mkdir(parents=True, exist_ok=True)
