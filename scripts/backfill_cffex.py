#!/usr/bin/env python
"""中金所股指期货持仓历史数据补全脚本。
从 akshare get_cffex_rank_table 逐日拉取 IF/IC/IH 会员持仓，写入 market.duckdb。

用法:
    python scripts/backfill_cffex.py --start 2026-06-15
"""
import argparse, os, sys, time
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

def trading_days(start: str, end: str) -> list[str]:
    from datetime import date, timedelta
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    days = []
    d = s
    while d <= e:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    return days

def main():
    parser = argparse.ArgumentParser(description="中金所 CFFEX 持仓历史补全")
    parser.add_argument("--start", default="2026-06-01")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("FEATURE_PLATFORM_STORAGE",
                          os.path.join(PROJECT_ROOT, "storage"))
    from backend.config import Settings
    from backend.services.collectors._common import ctx_dt
    from backend.services.collectors.writer import write_market
    from backend.services.collectors.akshare_src import fetch_cffex_rank_table

    settings = Settings()
    days = trading_days(args.start, args.end)
    print(f"交易日: {len(days)} 天 ({args.start} → {args.end})")

    if args.dry_run:
        return

    ok, fail = 0, 0
    for i, dt in enumerate(days):
        ctx = {"data_interval_end": dt}
        try:
            cols, rows = fetch_cffex_rank_table({}, ctx)
            if rows:
                n = write_market(settings, "ods_akshare_cffex_rank_table",
                                 dt, cols, rows)
                ok += 1
                if i % 5 == 0:
                    print(f"  {dt}: {n} rows ✅")
            else:
                print(f"  {dt}: no data (非交易日?)")
                ok += 1  # count as ok
        except Exception as e:
            fail += 1
            if fail <= 3:
                print(f"  {dt}: ERROR - {e}")
        if i < len(days) - 1:
            time.sleep(args.interval)

    print(f"\n完成: {ok} 成功, {fail} 失败")

if __name__ == "__main__":
    main()
