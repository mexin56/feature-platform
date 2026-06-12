"""数据查询:本地 DuckDB(本项目 parquet 特征快照自动注册同名视图)或数据源连接直查。
只读防呆(仅 SELECT/WITH/SHOW/DESCRIBE/EXPLAIN、单语句、限行返回)——LAN 工具的防误操作,
不是安全边界;viewer 角色由全局只读门禁挡在 POST 之外。"""
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..deps import get_current_user, get_db, get_project_id, get_settings
from ..models import FeatureGroup

router = APIRouter(tags=["query"])

MAX_ROWS = 500
READONLY_PREFIXES = ("select", "with", "show", "describe", "desc", "explain")


class QueryIn(BaseModel):
    engine: str  # duckdb | connection
    connection_id: int | None = None
    sql: str
    limit: int = Field(default=200, ge=1, le=MAX_ROWS)


def _guard(sql: str) -> str:
    s = sql.strip().rstrip(";").strip()
    if not s:
        raise HTTPException(400, "SQL 不能为空")
    if ";" in s:
        raise HTTPException(400, "仅支持单条查询语句")
    if s.split(None, 1)[0].lower() not in READONLY_PREFIXES:
        raise HTTPException(400, "仅支持只读查询(SELECT/WITH/SHOW/DESCRIBE/EXPLAIN)")
    return s


def _cell(v):
    """JSON 安全化:Decimal/date 等转字符串。"""
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


@router.post("/query")
def run_query(body: QueryIn, db=Depends(get_db), settings=Depends(get_settings),
              user=Depends(get_current_user), pid=Depends(get_project_id)):
    sql = _guard(body.sql)
    t0 = time.monotonic()
    if body.engine == "duckdb":
        cols, rows, views = _query_duckdb(db, settings, pid, sql, body.limit)
    elif body.engine == "connection":
        if not body.connection_id:
            raise HTTPException(400, "请选择连接")
        cols, rows = _query_connection(body, settings, sql)
        views = []
    else:
        raise HTTPException(400, "engine 须为 duckdb 或 connection")
    return {"columns": cols, "rows": rows, "row_count": len(rows),
            "truncated": len(rows) >= body.limit,
            "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
            "views": views}


def _query_duckdb(db, settings, pid, sql, limit):
    import duckdb

    con = duckdb.connect()
    try:
        views = set()
        fgs = db.scalars(select(FeatureGroup).where(
            FeatureGroup.project_id == pid,
            FeatureGroup.offline_kind == "parquet")).all()
        for fg in fgs:
            d = settings.offline_dir / fg.offline_location
            if d.is_dir() and any(d.glob("*.parquet")):
                con.sql(f'create or replace view "{fg.name}" as '
                        f"select * from read_parquet('{(d / '*.parquet').as_posix()}')")
                views.add(fg.name)
        cur = con.execute(sql)
        rows = cur.fetchmany(limit)
        cols = [c[0] for c in cur.description] if cur.description else []
        return cols, [[_cell(v) for v in r] for r in rows], sorted(views)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001  用户 SQL 错误统一转 400
        raise HTTPException(400, f"查询失败: {e}")
    finally:
        con.close()


def _query_connection(body: QueryIn, settings, sql):
    from ..services.plugins.materialize import _fetch_rows
    from ..services.plugins.sql_pushdown import _connection_info

    try:
        info = _connection_info({"connection_id": body.connection_id}, settings)
    except ValueError as e:
        raise HTTPException(404, str(e))
    # SELECT 可包一层行数限制下推;WITH/SHOW 等无法包裹 → 取回后截断
    if sql.split(None, 1)[0].lower() == "select":
        sql = f"select * from ({sql}) t limit {body.limit}"
    try:
        cols, rows = _fetch_rows(info, sql)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"查询失败: {e}")
    return cols, [[_cell(v) for v in r] for r in rows[: body.limit]]
