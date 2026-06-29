"""market.duckdb 写入器(PostgreSQL 版):统一附加 dt/collected_at 列,按 dt 幂等,并发安全。

两引擎共存:
  - market_engine=postgres: 写入 PostgreSQL(默认,多进程安全)
  - market_engine=duckdb:  写入 DuckDB 本地文件(旧模式,有写锁冲突)

PostgreSQL 建表逻辑:
  - 列名使用小写(TSQL通用),dt→dt, collected_at→collected_at
  - CREATE TABLE IF NOT EXISTS + DELETE WHERE dt= 幂等
  - 列类型根据首个非空 Python 值推断: int→BIGINT, float→DOUBLE, str→TEXT
"""
import os
import re
import time
from datetime import datetime
from pathlib import Path

TABLE_RE = re.compile(r"^[a-z0-9_]+$")
COL_RE = re.compile(r"^[A-Za-z0-9_一-鿿]+$")
_TYPE_MAP = ((bool, "BOOLEAN"), (int, "BIGINT"), (float, "DOUBLE PRECISION"), (str, "TEXT"))


def _col_type(value, rows):
    for r in rows:
        v = r[value]
        if v is None:
            continue
        for py, pg in _TYPE_MAP:
            if isinstance(v, py):
                return pg
        return "TEXT"
    return "TEXT"


def _pg_conn(settings):
    """获取 PostgreSQL 连接。"""
    import psycopg2
    return psycopg2.connect(settings.pg_url)


def write_market(settings, table: str, dt: str, columns: list[str],
                 rows: list[tuple], collected_at: str | None = None) -> int:
    """写入(覆盖当日)PostgreSQL 的 {table},返回插入行数。"""
    if not TABLE_RE.match(table or ""):
        raise ValueError(f"表名非法(仅小写字母/数字/下划线): {table}")
    for c in columns:
        if not COL_RE.match(c or ""):
            raise ValueError(f"列名非法: {c}")
    if "dt" in columns or "collected_at" in columns:
        raise ValueError("列名不得占用统一附加列 dt/collected_at")
    for r in rows:
        if len(r) != len(columns):
            raise ValueError(f"行宽 {len(r)} 与列数 {len(columns)} 不一致")
    collected_at = collected_at or datetime.now().isoformat(timespec="seconds")
    full_cols = list(columns) + ["dt", "collected_at"]
    data = [tuple(r) + (dt, collected_at) for r in rows]

    if getattr(settings, "market_engine", "postgres") == "duckdb":
        return _write_duckdb(settings, table, dt, columns, rows, collected_at)

    return _write_pg(settings, table, dt, full_cols, data)


def _write_pg(settings, table, dt, full_cols, data):
    """PostgreSQL 幂等写入(先删当日,再插)。"""
    import psycopg2

    conn = _pg_conn(settings)
    try:
        with conn.cursor() as cur:
            # 检查表是否存在
            cur.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=%s", [table])
            exists = cur.fetchone()[0]
            if exists:
                cur.execute(f'DELETE FROM "{table}" WHERE dt = %s', [dt])
            else:
                cols_types = []
                for i, c in enumerate(full_cols):
                    t = _col_type(i, [r[:-2] for r in data]) if i < len(full_cols) - 2 else "TEXT"
                    cols_types.append(f'"{c.lower()}" {t}')
                cur.execute(f'CREATE TABLE "{table}" ({", ".join(cols_types)})')
            if data:
                placeholders = ", ".join(["%s"] * len(full_cols))
                cols = ", ".join(f'"{c.lower()}"' for c in full_cols)
                cur.executemany(
                    f'INSERT INTO "{table}" ({cols}) VALUES ({placeholders})',
                    data)
        conn.commit()
    finally:
        conn.close()
    return len(data)


def _write_duckdb(settings, table, dt, columns, rows, collected_at):
    """DuckDB 写入(旧模式,保留兼容)。"""
    import duckdb as _d

    collected_at = collected_at or datetime.now().isoformat(timespec="seconds")
    db_path = Path(settings.market_db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    full_cols = list(columns) + ["dt", "collected_at"]
    data = [tuple(r) + (dt, collected_at) for r in rows]

    max_retries = 3
    for attempt in range(max_retries + 1):
        if attempt:
            time.sleep(attempt)
        try:
            con = _d.connect(str(db_path))
        except _d.IOException as e:
            if attempt < max_retries:
                continue
            raise RuntimeError(f"DuckDB 写锁冲突,重试 {max_retries} 次失败") from e
        try:
            exists = con.execute(
                "select count(*) from information_schema.tables "
                "where table_schema='main' and table_name=?", [table]).fetchone()[0]
            if exists:
                con.execute(f"delete from {table} where dt = ?", [dt])
            else:
                from .writer import _col_type as _ct
                types = [_ct(data, i) for i in range(len(columns))] + ["VARCHAR"] * 2
                cols_sql = ", ".join(f'"{c}" {t}' for c, t in zip(full_cols, types))
                con.execute(f"create table {table} ({cols_sql})")
            if data:
                collist = ", ".join(f'"{c}"' for c in full_cols)
                ph = ", ".join("?" for _ in full_cols)
                con.executemany(f"insert into {table} ({collist}) values ({ph})", data)
            return len(data)
        finally:
            con.close()
    raise RuntimeError(f"DuckDB 写入重试 {max_retries} 次仍失败")


def attach_market(con, settings) -> bool:
    """兼容旧接口:PostgreSQL 模式下不 attach,直接返回 False。"""
    if getattr(settings, "market_engine", "postgres") == "postgres":
        return False
    p = Path(getattr(settings, "market_db", "") or "")
    if not str(p) or not p.exists():
        return False
    try:
        con.execute(f"ATTACH '{p.as_posix()}' AS market (READ_ONLY)")
        return True
    except Exception:
        return False
