"""market.duckdb 写入器:统一附加 dt/collected_at 列,按 dt 幂等(先删后插)。
首跑按首个非空值推断列类型建表;无 pandas 依赖,executemany 参数化插入。"""
import re
from datetime import datetime
from pathlib import Path

TABLE_RE = re.compile(r"^[a-z0-9_]+$")
# 列名放行汉字(akshare/东财等源的 DataFrame 列名为中文);引号包裹建表,无注入面
COL_RE = re.compile(r"^[A-Za-z0-9_一-鿿]+$")
# bool 须先于 int 判定(bool 是 int 子类)
_TYPE_MAP = ((bool, "BOOLEAN"), (int, "BIGINT"), (float, "DOUBLE"), (str, "VARCHAR"))


def _col_type(rows: list[tuple], idx: int) -> str:
    for r in rows:
        v = r[idx]
        if v is None:
            continue
        for py, dk in _TYPE_MAP:
            if isinstance(v, py):
                return dk
        return "VARCHAR"
    return "VARCHAR"


def write_market(settings, table: str, dt: str, columns: list[str],
                 rows: list[tuple], collected_at: str | None = None) -> int:
    """写入(覆盖当日)market.duckdb 的 {table},返回插入行数。"""
    import duckdb

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
    db_path = Path(settings.market_db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    full_cols = list(columns) + ["dt", "collected_at"]
    data = [tuple(r) + (dt, collected_at) for r in rows]
    con = duckdb.connect(str(db_path))
    try:
        exists = con.execute(
            "select count(*) from information_schema.tables "
            "where table_schema='main' and table_name=?", [table]).fetchone()[0]
        if exists:
            con.execute(f"delete from {table} where dt = ?", [dt])
        else:
            types = [_col_type(data, i) for i in range(len(columns))] + ["VARCHAR"] * 2
            cols_sql = ", ".join(f'"{c}" {t}' for c, t in zip(full_cols, types))
            con.execute(f"create table {table} ({cols_sql})")
        if data:
            collist = ", ".join(f'"{c}"' for c in full_cols)
            ph = ", ".join("?" for _ in full_cols)
            con.executemany(f"insert into {table} ({collist}) values ({ph})", data)
        return len(data)
    finally:
        con.close()


def attach_market(con, settings) -> bool:
    """market.duckdb 存在时只读 ATTACH 为 market 库;返回是否已挂载。"""
    p = Path(getattr(settings, "market_db", "") or "")
    if not str(p) or not p.exists():
        return False
    con.execute(f"ATTACH '{p.as_posix()}' AS market (READ_ONLY)")
    return True
