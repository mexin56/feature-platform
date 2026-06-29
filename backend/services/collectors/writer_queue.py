"""写队列:task_runner 子进程将写入请求入队到 meta.db(SQLite),
主进程(poll / scheduler tick)统一 drain 到 market.duckdb。

避免多进程同时写 DuckDB 导致的写锁冲突。

队列表 write_queue:
  id         INTEGER PRIMARY KEY AUTOINCREMENT
  table      TEXT NOT NULL        -- 目标表名(ods_xxx)
  dt         TEXT NOT NULL        -- 数据日期 YYYY-MM-DD
  columns    TEXT NOT NULL        -- JSON 数组
  rows       TEXT NOT NULL        -- JSON 二维数组
  collected_at TEXT               -- 采集时间戳(ISO)
  status     TEXT DEFAULT 'pending'  -- pending/done
  created_at TEXT NOT NULL
"""
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from .writer import TABLE_RE, COL_RE, _col_type

QUEUE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS write_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name  TEXT NOT NULL,
    dt          TEXT NOT NULL,
    columns     TEXT NOT NULL,
    rows        TEXT NOT NULL,
    collected_at TEXT,
    status      TEXT DEFAULT 'pending',
    created_at  TEXT NOT NULL
)
"""


def _ensure_queue_table(db_path: str) -> None:
    """确保 meta.db 中有 write_queue 表。"""
    con = sqlite3.connect(db_path)
    try:
        con.execute(QUEUE_TABLE_SQL)
        con.commit()
    finally:
        con.close()


def enqueue(settings, table: str, dt: str, columns: list[str],
            rows: list[tuple], collected_at: str | None = None) -> int:
    """将写入请求加入队列(不直写 DuckDB)。返回行数。"""
    if not TABLE_RE.match(table or ""):
        raise ValueError(f"表名非法: {table}")
    for c in columns:
        if not COL_RE.match(c or ""):
            raise ValueError(f"列名非法: {c}")
    if "dt" in columns or "collected_at" in columns:
        raise ValueError("列名不得占用统一附加列 dt/collected_at")
    for r in rows:
        if len(r) != len(columns):
            raise ValueError(f"行宽 {len(r)} 与列数 {len(columns)} 不一致")

    collected_at = collected_at or datetime.now().isoformat(timespec="seconds")

    # JSON 序列化(全内存,rows 一般 ≤ 2000 行,单次立入)
    cols_json = json.dumps(columns, ensure_ascii=False)
    rows_json = json.dumps([[None if v is None else v for v in r] for r in rows],
                           ensure_ascii=False)
    now = datetime.now().isoformat(timespec="seconds")

    db_path = Path(settings.db_path)
    _ensure_queue_table(str(db_path))

    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO write_queue (table_name, dt, columns, rows, collected_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (table, dt, cols_json, rows_json, collected_at, now))
        con.commit()
    finally:
        con.close()
    return len(rows)


def drain_queue(settings, max_batch: int = 10) -> int:
    """从队列取至多 max_batch 条,依次写入 market.duckdb。
    返回处理条数。失败条目保留在队列中(status 不变 / 下次重试)。"""
    import duckdb

    db_path = Path(settings.db_path)
    _ensure_queue_table(str(db_path))

    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            "SELECT id, table_name, dt, columns, rows, collected_at "
            "FROM write_queue WHERE status = 'pending' ORDER BY id LIMIT ?",
            (max_batch,)).fetchall()
    finally:
        con.close()

    if not rows:
        return 0

    duck_path = Path(settings.market_db)
    duck_path.parent.mkdir(parents=True, exist_ok=True)

    processed = 0
    for qid, table_name, dt, cols_json, rows_json, collected_at in rows:
        columns = json.loads(cols_json)
        raw_rows = json.loads(rows_json)
        data = [tuple(r) for r in raw_rows]
        collected_at = collected_at or datetime.now().isoformat(timespec="seconds")

        full_cols = list(columns) + ["dt", "collected_at"]
        full_data = [tuple(r) + (dt, collected_at) for r in data]

        try:
            duck = duckdb.connect(str(duck_path))
            try:
                exists = duck.execute(
                    "select count(*) from information_schema.tables "
                    "where table_schema='main' and table_name=?", [table_name]).fetchone()[0]
                if exists:
                    duck.execute(f"delete from {table_name} where dt = ?", [dt])
                else:
                    types = [_col_type(full_data, i) for i in range(len(columns))] + ["VARCHAR"] * 2
                    cols_sql = ", ".join(f'"{c}" {t}' for c, t in zip(full_cols, types))
                    duck.execute(f"create table {table_name} ({cols_sql})")
                if full_data:
                    collist = ", ".join(f'"{c}"' for c in full_cols)
                    ph = ", ".join("?" for _ in full_cols)
                    duck.executemany(f"insert into {table_name} ({collist}) values ({ph})", full_data)
            finally:
                duck.close()
        except Exception:
            # DuckDB 写锁冲突等异常 → 保留队列中,下次重试
            time.sleep(1)
            continue

        # 标记为 done
        con2 = sqlite3.connect(str(db_path))
        try:
            con2.execute("UPDATE write_queue SET status = 'done' WHERE id = ?", (qid,))
            con2.commit()
        finally:
            con2.close()
        processed += 1

    return processed


def pending_count(settings) -> int:
    """队列中待处理条目数。"""
    db_path = Path(settings.db_path)
    _ensure_queue_table(str(db_path))
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute("SELECT count(*) FROM write_queue WHERE status = 'pending'").fetchone()
        return row[0] if row else 0
    finally:
        con.close()
