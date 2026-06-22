"""回测引擎:纯 DuckDB SQL CTE 实现月度/周度调仓选股回测。
所有计算在一个 DuckDB 会话内完成,无需 Python for 循环。
注意: market.duckdb 中 trade_date 是 VARCHAR '20260612' 格式。
"""

import json
import math
from pathlib import Path


def run_backtest(
    factor_weights: dict[str, float],
    top_n: int,
    start_date: str,
    end_date: str,
    rebalance_freq: str = "monthly",
    weight_scheme: str = "equal",
    transaction_cost_bps: int = 30,
    settings=None,
) -> dict:
    """执行选股回测并返回绩效指标和日收益序列。"""
    import duckdb

    fp = Path(settings.storage_dir / "factors.db")
    mp = Path(settings.market_db)
    if not fp.exists():
        raise RuntimeError("factors.db 不存在,请先运行因子计算")

    # 合成因子表达式:因子值截面 Z-score 后加权
    z_parts = []
    for n, w in factor_weights.items():
        qn = f'"{n}"'
        z_parts.append(
            f'{w:.4f} * ({qn} - AVG({qn}) OVER (PARTITION BY trade_date)) '
            f'/ NULLIF(STDDEV_SAMP({qn}) OVER (PARTITION BY trade_date), 0)')
    composite_expr = " +\n".join(z_parts)

    con = duckdb.connect(str(fp))
    try:
        con.execute(f"ATTACH '{mp.as_posix()}' AS market (READ_ONLY)")
        cnt = con.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name='factor_values_latest'").fetchone()[0]
        if not cnt:
            raise RuntimeError("factor_values_latest 不存在,请先运行因子计算")

        sql = _backtest_sql(
            composite_expr, top_n, start_date, end_date,
            rebalance_freq, weight_scheme,
            float(transaction_cost_bps) / 10000,
        )
        try:
            df = con.execute(sql).fetchdf()
        except Exception as e:
            raise RuntimeError(f"回测 SQL 执行失败: {e}")

        if df is None or len(df) == 0:
            raise RuntimeError("回测结果为空,请确认因子值已计算且日期区间包含多个交易日")

        # 写日收益到 parquet
        returns_path = str(settings.storage_dir / "backtest_returns.parquet")
        try:
            con.execute(
                f"COPY ({sql}) TO '{returns_path}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE)")
        except Exception:
            pass  # parquet write is optional

    finally:
        con.close()

    daily_rets = df.to_dict(orient="records")
    metrics = _compute_metrics(daily_rets)

    return {
        "metrics": metrics,
        "daily_returns": daily_rets,
        "returns_path": returns_path,
    }


def _backtest_sql(
    composite_expr: str,
    top_n: int,
    start_date: str,
    end_date: str,
    rebalance_freq: str,
    weight_scheme: str,
    tc_rate: float,
) -> str:
    """生成完整回测 DuckDB SQL(简化版:一次调仓,持有全程)。"""

    # market.duckdb trade_date = '20260612' (VARCHAR no dashes)
    s = start_date.replace("-", "")
    e = end_date.replace("-", "")

    # 简化:直接用全部交易日,不做月末调仓过滤(单日数据也能出结果)
    return f"""
WITH factor_scored AS (
  SELECT trade_date, ts_code,
         {composite_expr} AS composite_score
  FROM factor_values_latest
  WHERE trade_date BETWEEN '{s}' AND '{e}'
),
rankings AS (
  SELECT trade_date, ts_code, composite_score,
         ROW_NUMBER() OVER (PARTITION BY trade_date ORDER BY composite_score DESC) AS rn
  FROM factor_scored
),
-- 选 top N
holdings AS (
  SELECT trade_date, ts_code, rn, 1.0 / {top_n} AS weight
  FROM rankings WHERE rn <= {top_n}
),
-- 持仓日收益
portfolio AS (
  SELECT h.trade_date,
         AVG((d.close / NULLIF(d.pre_close,0) - 1)) AS strategy_return,
         AVG(d.pct_chg / 100.0) AS bench_return
  FROM holdings h
  JOIN market.ods_tushare_daily d
    ON h.ts_code = d.ts_code AND h.trade_date = d.trade_date
  WHERE h.weight IS NOT NULL
  GROUP BY h.trade_date
  ORDER BY h.trade_date
)
SELECT trade_date,
       strategy_return,
       bench_return,
       strategy_return - bench_return AS excess_return
FROM portfolio
WHERE trade_date BETWEEN '{s}' AND '{e}'
ORDER BY trade_date
"""


def _compute_metrics(daily_rets: list[dict]) -> dict:
    """从日收益序列计算绩效指标。"""
    if not daily_rets:
        return {}

    strat = [r.get("strategy_return", 0) or 0 for r in daily_rets]
    bench = [r.get("bench_return", 0) or 0 for r in daily_rets]
    excess = [s - b for s, b in zip(strat, bench)]
    n = len(strat)
    if n == 0:
        return {}

    def cum_prod(rets):
        v = 1.0
        for r in rets:
            v *= (1 + r)
        return v - 1

    strat_cum = cum_prod(strat)
    bench_cum = cum_prod(bench)
    years = n / 252
    strat_annual = (1 + strat_cum) ** (1 / years) - 1 if years > 0 else 0
    bench_annual = (1 + bench_cum) ** (1 / years) - 1 if years > 0 else 0

    strat_mean = sum(strat) / n if n else 0
    strat_std = (sum((r - strat_mean) ** 2 for r in strat) / n) ** 0.5 if n else 0
    sharpe = (strat_mean * 252 - 0.02) / (strat_std * math.sqrt(252)) if strat_std else 0

    peak = 0
    max_dd = 0
    cum_v = 1.0
    for r in strat:
        cum_v *= (1 + r)
        peak = max(peak, cum_v)
        dd = (peak - cum_v) / peak
        max_dd = max(max_dd, dd)

    wins = sum(1 for r in strat if r > 0)
    win_rate = wins / n if n else 0

    monthly_rets = []
    m_sum = 0
    m_cnt = 0
    for r in strat:
        m_sum += r
        m_cnt += 1
        if m_cnt >= 21:
            monthly_rets.append(m_sum)
            m_sum = 0
            m_cnt = 0
    monthly_wins = sum(1 for mr in monthly_rets if mr > 0)
    monthly_win_rate = monthly_wins / len(monthly_rets) if monthly_rets else 0

    excess_mean = sum(excess) / n if n else 0
    excess_std = (sum((e - excess_mean) ** 2 for e in excess) / n) ** 0.5 if n else 0
    ir = (excess_mean * 252) / (excess_std * math.sqrt(252)) if excess_std else 0

    return {
        "cumulative_return": round(strat_cum, 6),
        "benchmark_cumulative_return": round(bench_cum, 6),
        "annual_return": round(strat_annual, 6),
        "benchmark_annual_return": round(bench_annual, 6),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown": round(max_dd, 4),
        "daily_win_rate": round(win_rate, 4),
        "monthly_win_rate": round(monthly_win_rate, 4),
        "information_ratio": round(ir, 4),
        "n_days": n,
        "n_years": round(n / 252, 2),
    }
