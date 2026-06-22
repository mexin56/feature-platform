"""HS300 等指数成分股管理:时点版本化存储+从 tushare 刷新。
成分股按 (index_code, trade_date, ts_code) 三维存于 market.duckdb 的
ods_index_weight 表,支持时点查询——回测不可使用未来成分股(防幸存者偏差)。
无成分股数据时自动回退到 market.duckdb 中全部股票作为 universe。"""

from datetime import datetime
from pathlib import Path


INDEX_MAP = {
    "hs300": "000300.SH",
    "csi500": "000905.SH",
    "csi1000": "000852.SH",
    "sz50": "000016.SH",
    "gem": "399006.SZ",
    "star50": "000688.SH",
}


def index_code(name: str) -> str:
    """别名 → tushare index_code。"""
    return INDEX_MAP.get(name.lower(), name)


def get_constituents(index_name: str, as_of_date: str, settings, db=None) -> list[str]:
    """查询指数成分股;无数据时先尝试 tushare 刷新,再失败则回退到全市场股票。

    Returns:
        ts_code 列表(如 ['000001.SZ','600519.SH'…])。
    """
    import duckdb

    path = Path(settings.market_db)
    if not path.exists():
        raise RuntimeError(f"market.duckdb 不存在: {path}")

    icd = index_code(index_name)

    # 1. 尝试从本地 ods_index_weight 读
    con = duckdb.connect(str(path), read_only=True)
    try:
        cnt = con.execute(
            "select count(*) from information_schema.tables "
            "where table_name='ods_index_weight'"
        ).fetchone()[0]
        if cnt > 0:
            rows = con.execute(
                """select ts_code from ods_index_weight
                   where index_code = ? and trade_date <= ?
                   and trade_date = (select max(trade_date) from ods_index_weight
                                     where index_code = ? and trade_date <= ?)
                   order by ts_code""",
                [icd, as_of_date, icd, as_of_date],
            ).fetchall()
            if rows:
                return [r[0] for r in rows]
    finally:
        con.close()

    # 2. 尝试 tushare 实时拉取
    try:
        refresh(index_name, as_of_date, settings, db)
    except Exception:
        pass

    # 3. 再次尝试从本地读
    con = duckdb.connect(str(path), read_only=True)
    try:
        cnt = con.execute(
            "select count(*) from information_schema.tables "
            "where table_name='ods_index_weight'"
        ).fetchone()[0]
        if cnt > 0:
            rows = con.execute(
                """select ts_code from ods_index_weight
                   where index_code = ? and trade_date <= ?
                   and trade_date = (select max(trade_date) from ods_index_weight
                                     where index_code = ? and trade_date <= ?)
                   order by ts_code""",
                [icd, as_of_date, icd, as_of_date],
            ).fetchall()
            if rows:
                return [r[0] for r in rows]
    finally:
        con.close()

    # 4. 最终兜底:用 market.duckdb 中全部股票
    con = duckdb.connect(str(path), read_only=True)
    try:
        rows = con.execute(
            "select distinct ts_code from ods_tushare_daily "
            "where trade_date = (select max(trade_date) from ods_tushare_daily) "
            "order by ts_code"
        ).fetchall()
        if rows:
            return [r[0] for r in rows]
    finally:
        con.close()

    raise RuntimeError("market.duckdb 中无任何股票数据,请先运行数据采集或补全脚本")


def _read_tushare_token(db=None) -> str | None:
    import os

    token = os.environ.get("FP_TUSHARE_TOKEN")
    if token:
        return token
    if db:
        try:
            from ..models import SystemSetting
            row = db.query(SystemSetting).filter_by(key="tushare_token").first()
            if row and row.value:
                return row.value
        except Exception:
            pass
    return None


def refresh(index_name: str, trade_date: str, settings, db=None) -> int:
    """从 tushare 拉取指数成分股并写入 market.duckdb。"""
    from .collectors import _common as c
    from .collectors.tushare_client import get_pro
    from .collectors.writer import write_market

    icd = index_code(index_name)
    token = _read_tushare_token(db)
    pro = get_pro(token)
    td = c.nodash(trade_date)

    df = None
    last_err = None
    for api_name in ("index_weight", "index_member"):
        try:
            df = getattr(pro, api_name)(index_code=icd, trade_date=td)
            if df is not None and len(df) > 0:
                break
        except Exception as e:
            last_err = e
    if df is None or len(df) == 0:
        raise RuntimeError(
            f"tushare index_weight/index_member({icd}, {td}) 均无数据: {last_err}")

    cols, rows = c.df_to_table(df)
    if "index_code" not in cols:
        cols = ["index_code"] + cols
        rows = [tuple([icd]) + r for r in rows]
    if "trade_date" not in cols:
        cols = ["trade_date"] + cols
        rows = [tuple([td]) + r for r in rows]

    return write_market(
        settings, "ods_index_weight", trade_date, cols, rows,
        collected_at=datetime.now().isoformat(timespec="seconds"),
    )
