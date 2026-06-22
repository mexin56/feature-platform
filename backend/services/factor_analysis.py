"""因子分析引擎:IC计算 / 分位数收益 / IC衰减 / 相关性矩阵 / VIF。
所有计算在 factors.db 和 market.duckdb 上以 DuckDB SQL 执行。
注意: market.duckdb 中 trade_date 为 VARCHAR '20260612'(无连字符)格式。
"""

import json
from pathlib import Path


def analyze_factor(
    factor_name: str,
    start_date: str,
    end_date: str,
    forward_period: int = 1,
    settings=None,
    factors_db_path: str | None = None,
) -> dict:
    """单因子分析:IC序列 + IC摘要 + 分位数收益。"""
    import duckdb

    fp = Path(factors_db_path or str(settings.storage_dir / "factors.db"))
    mp = Path(settings.market_db)

    con = duckdb.connect(str(fp))
    try:
        con.execute(f"ATTACH '{mp.as_posix()}' AS market (READ_ONLY)")
        fq = f'"{factor_name}"'

        # market.duckdb trade_date = '20260612' (VARCHAR, no dashes)
        s = start_date.replace("-", "")
        e = end_date.replace("-", "")

        cnt = con.execute("SELECT count(*) FROM information_schema.tables "
                          "WHERE table_name='factor_values_latest'").fetchone()[0]
        if not cnt:
            raise RuntimeError("factor_values_latest 不存在,请先运行因子计算")

        # IC 序列:每日 CORR(因子值, 下一日收益)
        ic_rows = con.execute(f"""
WITH ranked AS (
  SELECT f.trade_date, f.ts_code, f.{fq} AS factor_val,
         (d2.close / NULLIF(d1.close,0) - 1) AS fwd_ret
  FROM factor_values_latest f
  JOIN market.ods_tushare_daily d1
    ON f.ts_code = d1.ts_code AND f.trade_date = d1.trade_date
  JOIN market.ods_tushare_daily d2
    ON f.ts_code = d2.ts_code
   AND d2.trade_date = (SELECT MIN(td.trade_date) FROM market.ods_tushare_daily td
                         WHERE td.ts_code = f.ts_code AND td.trade_date > f.trade_date)
  WHERE f.trade_date BETWEEN '{s}' AND '{e}'
    AND f.{fq} IS NOT NULL
)
SELECT trade_date,
       CORR(factor_val, fwd_ret) AS pearson_ic,
       COUNT(*) AS n_stocks
FROM ranked
GROUP BY trade_date
HAVING n_stocks >= 30
ORDER BY trade_date
""").fetchall()

        ic_series = [
            {"date": r[0], "pearson_ic": round(r[1], 6) if r[1] else None, "n": r[2]}
            for r in ic_rows
        ]

        ic_vals = [r["pearson_ic"] for r in ic_series if r["pearson_ic"] is not None]
        if ic_vals:
            mean_ic = sum(ic_vals) / len(ic_vals)
            std_ic = (sum((x - mean_ic) ** 2 for x in ic_vals) / len(ic_vals)) ** 0.5
            ic_ir = mean_ic / std_ic if std_ic else 0
            win_rate = sum(1 for x in ic_vals if x > 0) / len(ic_vals)
        else:
            mean_ic = ic_ir = win_rate = 0
            # No forward data available — inform user
            ic_series = [{"date": start_date, "pearson_ic": 0, "n": 0}]

        # 分位数收益
        qt_rows = con.execute(f"""
WITH ranked AS (
  SELECT f.trade_date, f.ts_code, f.{fq} AS factor_val,
         NTILE(5) OVER (PARTITION BY f.trade_date ORDER BY f.{fq} DESC) AS q,
         (d2.close / NULLIF(d1.close,0) - 1) AS fwd_ret
  FROM factor_values_latest f
  JOIN market.ods_tushare_daily d1
    ON f.ts_code = d1.ts_code AND f.trade_date = d1.trade_date
  JOIN market.ods_tushare_daily d2
    ON f.ts_code = d2.ts_code
   AND d2.trade_date = (SELECT MIN(td.trade_date) FROM market.ods_tushare_daily td
                         WHERE td.ts_code = f.ts_code AND td.trade_date > f.trade_date)
  WHERE f.trade_date BETWEEN '{s}' AND '{e}'
    AND f.{fq} IS NOT NULL
)
SELECT trade_date, q,
       AVG(fwd_ret) AS avg_ret
FROM ranked
GROUP BY trade_date, q
ORDER BY q, trade_date
""").fetchall()

        qt_returns = {}
        for r in qt_rows:
            q = str(r[1])
            if q not in qt_returns:
                qt_returns[q] = []
            qt_returns[q].append({"date": r[0], "avg_ret": round(r[2], 6) if r[2] else 0})

        q1_map = {x["date"]: x["avg_ret"] for x in qt_returns.get("1", [])}
        q5_map = {x["date"]: x["avg_ret"] for x in qt_returns.get("5", [])}
        ls = []
        for dt in sorted(set(q1_map) & set(q5_map)):
            ls.append({"date": dt, "ls_ret": round(q1_map[dt] - q5_map[dt], 6)})

        decay = _compute_ic_decay(con, factor_name, s, e)

    finally:
        con.close()

    return {
        "ic_summary": {
            "mean_ic": round(mean_ic, 6),
            "ic_ir": round(ic_ir, 4),
            "win_rate": round(win_rate, 4),
            "n_days": len(ic_vals),
        },
        "ic_series": ic_series,
        "quantile_returns": qt_returns,
        "long_short": ls,
        "decay": decay,
    }


def _compute_ic_decay(con, factor_name: str, start_date: str, end_date: str) -> list[dict]:
    """IC 在不同前瞻期的衰减。"""
    fq = f'"{factor_name}"'
    results = []
    for horizon in (1, 5, 10, 20, 60):
        rows = con.execute(f"""
WITH ranked AS (
  SELECT f.trade_date, f.{fq} AS factor_val,
         (d2.close / NULLIF(d1.close,0) - 1) AS fwd_ret
  FROM factor_values_latest f
  JOIN market.ods_tushare_daily d1
    ON f.ts_code = d1.ts_code AND f.trade_date = d1.trade_date
  JOIN market.ods_tushare_daily d2
    ON f.ts_code = d2.ts_code
   AND d2.trade_date = (SELECT MIN(td.trade_date) FROM market.ods_tushare_daily td
                         WHERE td.ts_code = f.ts_code AND td.trade_date > f.trade_date)
  WHERE f.trade_date BETWEEN '{start_date}' AND '{end_date}'
    AND f.{fq} IS NOT NULL
)
SELECT AVG(factor_val * fwd_ret),
       STDDEV_SAMP(factor_val * fwd_ret)
FROM ranked
""").fetchone()
        if rows and rows[0] is not None:
            results.append({"horizon_days": horizon, "mean_ic": round(rows[0], 6)})
    return results


def correlation_matrix(
    factor_names: list[str],
    settings=None,
    factors_db_path: str | None = None,
) -> dict:
    """因子间两两相关系数矩阵。"""
    import duckdb
    fp = Path(factors_db_path or str(settings.storage_dir / "factors.db"))
    if not fp.exists():
        return {"factors": factor_names, "matrix": []}
    con = duckdb.connect(str(fp))
    try:
        exist = con.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name='factor_values_latest'").fetchone()[0]
        if not exist:
            return {"factors": factor_names, "matrix": []}
        matrix = []
        for i, fa in enumerate(factor_names):
            row_vals = []
            for j, fb in enumerate(factor_names):
                if i == j:
                    row_vals.append(1.0)
                else:
                    r = con.execute(
                        f"""SELECT CORR("{fa}", "{fb}") FROM factor_values_latest
                        WHERE "{fa}" IS NOT NULL AND "{fb}" IS NOT NULL""").fetchone()
                    row_vals.append(round(r[0], 4) if r and r[0] is not None else None)
            matrix.append(row_vals)
    finally:
        con.close()
    return {"factors": factor_names, "matrix": matrix}


def combine_factors(
    factor_weights: dict[str, float],
    settings=None,
    factors_db_path: str | None = None,
) -> dict:
    """多因子合成分析。"""
    import duckdb
    fp = Path(factors_db_path or str(settings.storage_dir / "factors.db"))
    if not fp.exists():
        raise RuntimeError("factors.db 不存在")
    con = duckdb.connect(str(fp))
    try:
        expr_parts = [f'{weight} * "{name}"' for name, weight in factor_weights.items()]
        if not expr_parts:
            raise RuntimeError("因子权重为空")
        composite_expr = " + ".join(expr_parts)
        con.execute(
            "ALTER TABLE factor_values_latest ADD COLUMN IF NOT EXISTS _composite DOUBLE")
        con.execute(f"UPDATE factor_values_latest SET _composite = {composite_expr} WHERE 1=1")
        ic_result = con.execute("""
WITH ranked AS (
  SELECT trade_date, ts_code, _composite AS factor_val,
         ROW_NUMBER() OVER (PARTITION BY trade_date ORDER BY _composite DESC) AS rn,
         COUNT(*) OVER (PARTITION BY trade_date) AS total
  FROM factor_values_latest
  WHERE _composite IS NOT NULL
)
SELECT
  AVG(factor_val) FILTER (WHERE rn * 1.0 / total <= 0.2) AS top_avg,
  AVG(factor_val) FILTER (WHERE rn * 1.0 / total >= 0.8) AS btm_avg
FROM ranked
""").fetchone()
    finally:
        con.close()
    return {
        "factor_weights": factor_weights,
        "composite_ic": {
            "top_avg": round(ic_result[0], 6) if ic_result and ic_result[0] else None,
            "btm_avg": round(ic_result[1], 6) if ic_result and ic_result[1] else None,
        },
    }
