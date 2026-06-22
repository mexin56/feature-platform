"""因子计算引擎:将因子 SQL 定义织入单条 DuckDB mega-SQL,产出宽表到 factors.db。"""
from pathlib import Path


def compute_factors(
    factor_specs: list[dict],
    start_date: str,
    end_date: str,
    universe_codes: list[str],
    settings,
    factors_db_path: str | None = None,
    normalize: str = "",
) -> dict:
    """执行一批因子计算,写回 DuckDB 宽表。
    Returns: {rows, output_path, factor_names}
    """
    import duckdb

    fp = Path(factors_db_path or str(settings.storage_dir / "factors.db"))
    fp.parent.mkdir(parents=True, exist_ok=True)
    mp = Path(settings.market_db)
    if not mp.exists():
        raise RuntimeError(f"market.duckdb 不存在: {mp}")

    con = duckdb.connect(str(fp))
    try:
        con.execute(f"ATTACH '{mp.as_posix()}' AS market (READ_ONLY)")
        sql = _build_mega_sql(factor_specs, start_date, end_date, universe_codes, normalize)
        if not sql:
            raise RuntimeError("无有效因子,无法生成 SQL")
        table_name = "factor_values_latest"
        con.execute(f"DROP TABLE IF EXISTS {table_name}")
        con.execute(f"CREATE TABLE {table_name} AS ({sql})")
        row_count = con.execute(f"SELECT count(*) FROM {table_name}").fetchone()[0]
        factor_names = [f["name"] for f in factor_specs]
    finally:
        con.close()
    return {"rows": row_count, "output_path": str(fp), "factor_names": factor_names}


def _build_mega_sql(factor_specs, start_date, end_date, universe_codes, normalize) -> str:
    """构建 DuckDB SQL CTE,支持大 universe (>500只) 用 IN 分批。"""
    tables_needed = set()
    for f in factor_specs:
        for t in f.get("required_tables", "ods_tushare_daily").split(","):
            tables_needed.add(t.strip())

    needs_daily = "ods_tushare_daily" in tables_needed
    needs_basic = "ods_tushare_daily_basic" in tables_needed

    # market.duckdb 中 trade_date 是 VARCHAR '20260612' 格式
    s = start_date.replace("-", "")
    e = end_date.replace("-", "")

    # 大 universe: IN 分批
    batch_size = 200
    batches = [universe_codes[i:i+batch_size] for i in range(0, len(universe_codes), batch_size)]
    code_filter = " OR ".join(
        f"ts_code IN ({', '.join(repr(c) for c in b)})" for b in batches)

    parts = []
    if needs_daily:
        parts.append(f"""daily AS (
  SELECT * FROM market.ods_tushare_daily
  WHERE ({code_filter})
    AND trade_date BETWEEN '{s}' AND '{e}'
)""")
    if needs_basic:
        parts.append(f"""daily_basic AS (
  SELECT * FROM market.ods_tushare_daily_basic
  WHERE ({code_filter})
    AND trade_date BETWEEN '{s}' AND '{e}'
)""")

    # merge
    if needs_daily and needs_basic:
        merge = """SELECT d.*, b.turnover_rate, b.turnover_rate_f, b.volume_ratio,
         b.pe, b.pe_ttm, b.pb, b.ps, b.ps_ttm,
         b.dv_ratio, b.dv_ttm, b.total_share, b.float_share,
         b.free_share, b.total_mv, b.circ_mv
  FROM daily d
  LEFT JOIN daily_basic b ON d.ts_code = b.ts_code AND d.trade_date = b.trade_date"""
    elif needs_daily:
        merge = "SELECT * FROM daily"
    else:
        merge = "SELECT * FROM daily_basic"
    parts.append(f"merged AS ({merge})")

    factor_exprs = [f"    ({f['formula_sql']}) AS \"{f['name']}\"" for f in factor_specs]
    factors_cte = ",\n".join(factor_exprs)
    parts.append(f"""final AS (
  SELECT trade_date, ts_code,
{factors_cte}
  FROM merged m
  WHERE m.trade_date BETWEEN '{s}' AND '{e}'
)
SELECT * FROM final ORDER BY trade_date, ts_code""")

    return "WITH\n" + ",\n".join(parts)


def normalize_factors(factor_names: list[str], factors_db_path: str) -> int:
    """截面 Z-score 归一化(原地更新)。"""
    import duckdb
    fp = Path(factors_db_path)
    if not fp.exists():
        raise RuntimeError(f"factors.db 不存在: {fp}")
    con = duckdb.connect(str(fp))
    try:
        cnt = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name='factor_values_latest'"
        ).fetchone()[0]
        if not cnt:
            raise RuntimeError("factor_values_latest 不存在,请先运行因子计算")
        set_pairs = []
        for col in factor_names:
            cq = f'"{col}"'
            set_pairs.append(
                f"{cq} = ({cq} - AVG({cq}) OVER (PARTITION BY trade_date)) "
                f"/ NULLIF(STDDEV_SAMP({cq}) OVER (PARTITION BY trade_date), 0)")
        if set_pairs:
            con.execute(f"UPDATE factor_values_latest SET {', '.join(set_pairs)}")
        return con.execute("SELECT count(*) FROM factor_values_latest").fetchone()[0]
    finally:
        con.close()
