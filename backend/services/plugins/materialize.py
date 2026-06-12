"""materialize 插件:离线特征 → 在线存储,按水位(event_time 字符串序)增量,幂等 upsert。
params: {feature_group_id, connection_id?(warehouse 来源必填)}
parquet 来源:duckdb 读 offline_dir/<location>/*.parquet;
warehouse 来源:经连接 SELECT(复用 sql_pushdown 连接抽象)。
event_time 口径:ISO 字符串比较;水位仅在可解析为日期/时间时推进。
迟到数据:event_time <= 水位的行不会被增量拾取;如需补录请重置特征组水位后重跑物化。"""
import json
from datetime import datetime

from sqlalchemy.orm import sessionmaker

from ..online_store import upsert
from .sql_pushdown import _connection_info


def _fetch_rows(conn_info: tuple, sql: str) -> tuple[list[str], list[tuple]]:
    """warehouse 取数:返回 (列名列表, 行元组列表)。"""
    conn_type, host, port, username, password, database = conn_info
    if conn_type == "mysql":
        import pymysql

        conn = pymysql.connect(host=host, port=port, user=username, password=password,
                               database=database or None, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                cols = [d[0] for d in cur.description]
                return cols, cur.fetchall()
        finally:
            conn.close()
    elif conn_type == "spark":
        from pyhive import hive

        conn = hive.connect(host=host, port=port, username=username or None,
                            database=database or "default")
        try:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                cols = [d[0].split(".")[-1] for d in cur.description]
                return cols, cur.fetchall()
            finally:
                cur.close()
        finally:
            conn.close()
    raise ValueError(f"不支持的连接类型: {conn_type}")


def _parse_watermark(et: str | None) -> datetime | None:
    if not et:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(et, fmt)
        except ValueError:
            continue
    return None


def execute(params: dict, ctx: dict, env) -> dict:
    from ...db import make_engine
    from ...models import FeatureGroup

    engine = make_engine(env.db_path)
    try:
        Session = sessionmaker(bind=engine)
        with Session() as db:
            fg = db.get(FeatureGroup, params["feature_group_id"])
            if fg is None:
                raise ValueError("特征组不存在")
            if not fg.online_enabled:
                raise ValueError(f"特征组 {fg.name} 未启用在线服务")
            entity_keys = json.loads(fg.entity_keys_json)
            et_col = fg.event_time_col
            wm = fg.materialize_watermark
            # 统一 datetime 格式化:午夜也输出完整时间串,避免午夜水位与当日数据的字符串序错误比较。
            # 纯日期数据('YYYY-MM-DD')与完整时间串('YYYY-MM-DD HH:MM:SS')比较仍正确:
            # '2026-06-15' < '2026-06-15 00:00:00'(前缀关系,两者都不重复)。
            wm_str = wm.strftime("%Y-%m-%d %H:%M:%S") if wm else None
            kind, location = fg.offline_kind, fg.offline_location

        rows = _load_rows(params, env, kind, location, et_col, wm_str)
        n, max_et = upsert(env.online_db_path, params["feature_group_id"], rows,
                           entity_keys, et_col)
        new_wm = _parse_watermark(max_et)
        if new_wm is not None:
            with Session() as db:
                fg = db.get(FeatureGroup, params["feature_group_id"])
                if fg.materialize_watermark is None or new_wm > fg.materialize_watermark:
                    fg.materialize_watermark = new_wm
                    db.commit()
        return {"rows": n, "watermark": max_et}
    finally:
        engine.dispose()


def _load_rows(params: dict, env, kind: str, location: str,
               et_col: str | None, wm_str: str | None) -> list[dict]:
    if kind == "parquet":
        import duckdb

        pattern = (env.offline_dir / location / "*.parquet").as_posix()
        sql = f"select * from read_parquet('{pattern}')"
        if et_col and wm_str:
            sql += f" where cast(\"{et_col}\" as varchar) > '{wm_str}'"
        rel = duckdb.sql(sql)
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, r)) for r in rel.fetchall()]
    if kind == "warehouse":
        if not params.get("connection_id"):
            raise ValueError("warehouse 来源须提供 connection_id")
        info = _connection_info(params, env)
        sql = f"select * from {location}"
        if et_col and wm_str:
            sql += f" where `{et_col}` > '{wm_str}'"
        cols, tuples = _fetch_rows(info, sql)
        return [dict(zip(cols, r)) for r in tuples]
    raise ValueError(f"未知离线落地类型: {kind}")
