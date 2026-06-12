from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def make_engine(db_path):
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")  # 调度器线程+子进程并发写库防锁
        cur.close()

    return engine


def ensure_column(engine, table: str, column: str, ddl: str) -> None:
    """SQLite 轻量迁移:列不存在则 ALTER TABLE 补列(兼容既有库)。"""
    with engine.connect() as conn:
        cols = [r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})")]
        if column not in cols:
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            conn.commit()
