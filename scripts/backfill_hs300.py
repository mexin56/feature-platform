#!/usr/bin/env python
"""沪深 300 历史行情数据补全脚本。
从 tushare 拉取 1-3 年日线+每日指标,写入 market.duckdb。

用法:
    cd E:/hermes/feature-platform
    python scripts/backfill_hs300.py --start 2023-01-01

技术要点:
- 逐股使用 pro_bar(adj=qfq) 批量取日线(一次调取整段区间),聚合后一次性写入
- daily_basic 按日 snapshot(trade_date 参数),逐日写入(幂等:同日 DELETE+INSERT)
- 成分股从 tushare index_weight 获取最新一期沪深 300 名单
- 限频:每只股票间 sleep 0.3s(pro_bar);daily_basic 按批限频
- tushare token: env FP_TUSHARE_TOKEN > meta.db > tushare_client 内置默认
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def trading_days(start: str, end: str) -> list[str]:
    """区间内所有工作日(周一至周五简化版)。"""
    from datetime import date

    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    days = []
    d = s
    while d <= e:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def _ts_code(sym: str) -> str:
    """裸代码 → tushare ts_code。"""
    s = str(sym).strip()
    if "." in s:
        return s
    if s.startswith("6"):
        return f"{s}.SH"
    if s.startswith(("4", "8")):
        return f"{s}.BJ"
    return f"{s}.SZ"


def main():
    parser = argparse.ArgumentParser(description="沪深 300 历史行情补全")
    parser.add_argument("--start", default="2024-01-01",
                        help="起始日期 YYYY-MM-DD(默认 2024-01-01)")
    parser.add_argument("--end", default=None,
                        help="结束日期 YYYY-MM-DD(默认今天)")
    parser.add_argument("--symbols-file", default=None,
                        help="股票代码文件(每行一个,不传则从 index_weight 获取最新成分股)")
    parser.add_argument("--interval-sec", type=float, default=0.3,
                        help="逐股调用间隔秒(默认 0.3)")
    parser.add_argument("--skip-daily-basic", action="store_true",
                        help="跳过每日指标(更快完成)")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅打印计划,不实际拉取")

    args = parser.parse_args()
    end_date = args.end or datetime.now().strftime("%Y-%m-%d")

    os.environ.setdefault("FEATURE_PLATFORM_STORAGE",
                          os.path.join(PROJECT_ROOT, "storage"))
    from backend.config import Settings
    settings = Settings()

    env_token = os.environ.get("FP_TUSHARE_TOKEN")

    if args.symbols_file:
        with open(args.symbols_file) as f:
            symbols = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        print(f"从文件读取 {len(symbols)} 个代码")
    else:
        from backend.services.collectors.tushare_client import get_pro
        pro = get_pro(env_token)
        # 尝试取最新的成分股
        td = end_date.replace("-", "")
        df_w = None
        for api_name in ("index_weight", "index_member"):
            try:
                df_w = getattr(pro, api_name)(index_code="000300.SH", trade_date=td)
                if df_w is not None and len(df_w) > 0:
                    break
            except Exception:
                continue
        if df_w is None or len(df_w) == 0:
            print("ERROR: 无法获取 HS300 成分股,tushare API 不可用")
            print("  请: 1) 设置环境变量 FP_TUSHARE_TOKEN  2) 或用 --symbols-file 指定代码文件")
            sys.exit(1)
        # index_weight 列名可能是 con_code 或 ts_code
        code_col = next((c for c in ("con_code", "ts_code") if c in df_w.columns), df_w.columns[0])
        symbols = sorted(df_w[code_col].dropna().unique().tolist())
        print(f"HS300 成分股 {len(symbols)} 只(从 {code_col} 列读取)")

    if args.dry_run:
        td_list = trading_days(args.start, end_date)
        print(f"交易日期数(工作日): {len(td_list)}")
        print(f"股票数: {len(symbols)}")
        print(f"预计日线行数: {len(td_list) * len(symbols):,}")
        print(f"预计 API 调用: {len(symbols)} 次 pro_bar + {len(td_list)} 次 daily_basic")
        return

    from backend.services.collectors import _common as c
    from backend.services.collectors.tushare_client import get_pro, pro_bar
    from backend.services.collectors.writer import write_market

    pro = get_pro(env_token)
    start_nodash = args.start.replace("-", "")
    end_nodash = end_date.replace("-", "")

    # ── 日线:逐股 pro_bar → 聚合 → 一次性写入 ──
    print(f"\n=== 日线 {args.start} → {end_date} ===")
    all_cols = None
    all_rows = []
    fail_count = 0

    for i, sym in enumerate(symbols):
        if i:
            time.sleep(args.interval_sec)
        try:
            ts = _ts_code(sym)
            df = pro_bar(pro, ts_code=ts, adj="qfq",
                         start_date=start_nodash, end_date=end_nodash)
            if df is None or len(df) == 0:
                continue
            cols, rows = c.df_to_table(df)
            # 确保有 ts_code 列(pro_bar 返回自带,但安全起见保留检查)
            if all_cols is None:
                all_cols = list(cols)
                # 把 ts_code 移到第一列
                if "ts_code" in all_cols and all_cols[0] != "ts_code":
                    tc_idx = all_cols.index("ts_code")
                    all_cols = ["ts_code"] + [c for j, c in enumerate(all_cols) if j != tc_idx]
            # 按统一列序排列
            idx_map = {c: i for i, c in enumerate(cols)}
            for r in rows:
                vals = []
                for col in all_cols:
                    if col in idx_map:
                        vals.append(r[idx_map[col]])
                    else:
                        vals.append(None)
                all_rows.append(tuple(vals))
            if (i + 1) % 50 == 0:
                print(f"  日线 {i+1}/{len(symbols)} 累计 {len(all_rows):,} 行")
        except Exception as e:
            fail_count += 1
            if fail_count <= 5:
                print(f"  WARN: {_ts_code(sym)} 日线失败: {e}")

    if all_rows:
        n = write_market(settings, "ods_tushare_daily", end_date,
                         all_cols, all_rows)
        print(f"  日线写入 {n:,} 行(覆盖 dt={end_date})")
    else:
        print("  WARN: 日线无数据!")
    if fail_count:
        print(f"  失败 {fail_count}/{len(symbols)} 只")

    # ── 每日指标:逐日 snapshot ──
    if not args.skip_daily_basic:
        print(f"\n=== 每日指标 {args.start} → {end_date} ===")
        td_list = trading_days(args.start, end_date)
        total_basic = 0
        basic_fails = 0
        for j, td in enumerate(td_list):
            td_nodash = td.replace("-", "")
            try:
                df = pro.daily_basic(trade_date=td_nodash)
                if df is None or len(df) == 0:
                    continue
                cols, rows = c.df_to_table(df)
                n = write_market(settings, "ods_tushare_daily_basic", td, cols, rows)
                total_basic += n
                if (j + 1) % 30 == 0:
                    print(f"  指标 {j+1}/{len(td_list)} {td} 累计 {total_basic:,} 行")
            except Exception as e:
                basic_fails += 1
                if basic_fails <= 3:
                    print(f"  WARN: {td} 每日指标失败: {e}")
            if j < len(td_list) - 1:
                time.sleep(0.15)
        print(f"  指标完成 {total_basic:,} 行,失败 {basic_fails} 天")

    print("\n=== 全部完成 ===")


if __name__ == "__main__":
    main()
