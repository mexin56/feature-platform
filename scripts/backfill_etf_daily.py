#!/usr/bin/env python
"""ETF 日线历史数据补全脚本:从 QMT(xtquant) 拉取全市场 ETF 日线,
写入 market.duckdb 的 ods_qmt_etf_daily 表,按交易日分片写入(幂等)。

用法:
    cd E:/hermes/feature-platform
    python scripts/backfill_etf_daily.py --start 2026-01-01 --end 2026-06-29
    python scripts/backfill_etf_daily.py --start 2026-01-01                         # 默认到当天
    python scripts/backfill_etf_daily.py --start 2025-01-01 --end 2025-12-31         # 2025全年

技术要点:
- 全量 ETF(1635+)一次性下载 + 批量读取本地缓存
- 含赛道分类(基于 ETF 名称关键词)
- 按 trade_date 逐日分片写入(dt=交易日期),幂等覆盖
- 断点续传:已有数据的日期自动跳过(--force 强制执行)
- 进度/失败明细实时打印

依赖: xtquant(需本机 QMT 终端运行中)
"""
import argparse
import os
import sys
import time
from collections import Counter
from datetime import datetime, date, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


# ── 赛道关键词(与 etf_src.py 同步) ──
SECTOR_KEYWORDS = [
    ('半导体', ['半导体', '芯片', '集成电路', '晶圆', '光刻', '封测']),
    ('创新药/医药', ['创新药', '医药', '医疗', '生物', '药ETF', '医美', '疫苗', '中药']),
    ('新能源车', ['新能源车', '新能源汽车', '锂电池', '锂电', '充电桩', '智能驾驶']),
    ('光伏/能源', ['光伏', '太阳能', '风电', '碳中和', '能源', '氢能', '储能']),
    ('消费', ['消费', '白酒', '食品', '饮料', '家电', '旅游', '免税', '养殖', '农业', '畜牧']),
    ('金融', ['金融', '银行', '证券', '保险', '地产', '券商']),
    ('科技互联', ['科技', '互联', '通信', '5G', 'AI', '人工智能', '大数据', '云计算', '软件', '计算机', '机器人', '数字经济', '信创', '算力', '游戏', '传媒']),
    ('军工', ['军工', '国防', '航天', '航空', '船舶']),
    ('周期资源', ['煤炭', '钢铁', '有色', '化工', '石油', '原油', '资源', '稀土', '建材', '黄金']),
    ('港股/中概', ['港股', '恒生', 'H股', '中概', '沪港深', '港股通', '港通']),
    ('红利/策略', ['红利', '股息', '低波', '价值', '质量', 'ESG', '基本面', '成长', '龙头']),
    ('债券/货币', ['国债', '证金', '可转债', '短融', '政金', '国开', '货币', '债券', '利率']),
    ('宽基指数', ['沪深300', '中证500', '中证1000', '上证50', '创业板', '科创50', '深证', '中证A', 'MSCI', '中证800', '中证2000']),
]


def classify_etf(name: str) -> str:
    for sector, keywords in SECTOR_KEYWORDS:
        for kw in keywords:
            if kw in name:
                return sector
    return "其他"


def _decode_name(raw_name: str) -> str:
    try:
        return raw_name.encode("latin1").decode("utf-8", errors="replace")
    except Exception:
        return raw_name


def trading_days(start: str, end: str) -> list[str]:
    """区间内所有周一至周五,用于说明/统计。"""
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    days = []
    d = s
    while d <= e:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def existing_dates(con, table: str) -> set[str]:
    """查询已有数据的所有 dt 值。"""
    try:
        rows = con.execute(f"select distinct dt from {table}").fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


def main():
    parser = argparse.ArgumentParser(description="ETF 日线历史数据补全(QMT)")
    parser.add_argument("--start", default="2026-01-01",
                        help="起始日期 YYYY-MM-DD (默认 2026-01-01)")
    parser.add_argument("--end", default=None,
                        help="结束日期 YYYY-MM-DD (默认当天)")
    parser.add_argument("--force", action="store_true",
                        help="强制覆盖已有数据(默认跳过已有日期)")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅打印计划,不实际拉取")
    args = parser.parse_args()
    end = args.end or datetime.now().strftime("%Y-%m-%d")

    os.environ.setdefault("FEATURE_PLATFORM_STORAGE",
                          os.path.join(PROJECT_ROOT, "storage"))
    from backend.config import Settings
    from backend.services.collectors.writer import write_market

    settings = Settings()

    # ── 1. 连接 QMT ──
    print("=== 连接 QMT xtdata ===")
    from xtquant import xtdata
    xtdata.enable_hello = False

    codes = xtdata.get_stock_list_in_sector("沪深ETF")
    total_etf = len(codes)
    print(f"ETF 总数: {total_etf}")

    # ── 2. 获取名称(只需一次) ──
    print("获取 ETF 基础信息...")
    details = xtdata.get_instrument_detail_list(codes)
    name_map = {}
    for code in codes:
        raw = details.get(code, {})
        raw_name = raw.get("InstrumentName", code)
        name = _decode_name(raw_name)
        name_map[code] = name

    # ── 3. 批量下载历史数据 ──
    start_nodash = args.start.replace("-", "")
    end_nodash = end.replace("-", "")
    print(f"\n批量下载 {total_etf} 只 ETF 日线: {args.start} ~ {end}")
    t0 = time.time()
    for i, code in enumerate(codes):
        xtdata.download_history_data(code, "1d", start_nodash, end_nodash)
        if (i + 1) % 200 == 0:
            print(f"  下载进度: {i+1}/{total_etf} ({100*(i+1)/total_etf:.0f}%)")
    print(f"下载完成: {time.time()-t0:.1f}s")

    # ── 4. 批量读取本地缓存 ──
    print("\n读取本地缓存...")
    all_frames: dict[str, list] = {}  # code -> [(date, open, high, low, close, volume, amount)]
    for i, code in enumerate(codes):
        result = xtdata.get_local_data(
            field_list=["open", "high", "low", "close", "volume", "amount"],
            stock_list=[code],
            period="1d",
            start_time=start_nodash,
            end_time=end_nodash,
            count=-1,
            dividend_type="front",
        )
        if code in result and not result[code].empty:
            df = result[code]
            rows = []
            for idx in df.itertuples():
                if hasattr(idx, "Index"):
                    d = str(idx.Index.date()) if hasattr(idx.Index, "date") else str(idx.Index)
                else:
                    d = str(df.index[df.index.get_loc(idx)])
                open_ = getattr(idx, "open", None)
                high_ = getattr(idx, "high", None)
                low_ = getattr(idx, "low", None)
                close_ = getattr(idx, "close", None)
                volume_ = getattr(idx, "volume", None)
                amount_ = getattr(idx, "amount", None)
                rows.append((d, open_, high_, low_, close_, volume_, amount_))
            if rows:
                all_frames[code] = rows
        if (i + 1) % 200 == 0:
            print(f"  读取进度: {i+1}/{total_etf}")

    print(f"有数据 ETF: {len(all_frames)}/{total_etf}")
    total_day_rows = sum(len(v) for v in all_frames.values())
    print(f"总日线行数: {total_day_rows:,}")

    if args.dry_run:
        td_list = trading_days(args.start, end)
        print(f"\n工作日数: {len(td_list)}")
        print(f"预计写入行数: {total_day_rows:,}")
        print(f"预计每条写入行数: ~{total_day_rows // max(len(td_list), 1)}/天")
        return

    # ── 5. 按交易日分片写入 ──
    # 构建 date->rows 的倒排索引
    print("\n按交易日分片写入...")
    date_rows: dict[str, list[tuple]] = {}
    sector_cache: dict[str, str] = {}

    for code, items in all_frames.items():
        name = name_map.get(code, code)
        sector = sector_cache.get(code)
        if sector is None:
            sector = classify_etf(name)
            sector_cache[code] = sector
        short = code.replace(".SH", "").replace(".SZ", "")

        for d, open_, high_, low_, close_, volume_, amount_ in items:
            if close_ is None:
                continue
            try:
                change_pct = round((float(close_) / float(close_) - 1) * 100, 2)
            except (TypeError, ValueError, ZeroDivisionError):
                change_pct = 0.0
            row = (short, name, sector,
                   float(open_) if open_ is not None else None,
                   float(high_) if high_ is not None else None,
                   float(low_) if low_ is not None else None,
                   float(close_),
                   int(volume_) if volume_ is not None else None,
                   float(amount_) if amount_ is not None else None,
                   change_pct,
                   None,  # pre_close 在日线中没有
                   d)     # trade_date
            date_rows.setdefault(d, []).append(row)

    columns = [
        "symbol", "name", "sector",
        "open", "high", "low", "close",
        "volume", "amount", "change_pct",
        "pre_close", "trade_date",
    ]

    table = "ods_qmt_etf_daily"

    # 检查已有日期
    import duckdb
    con = duckdb.connect(str(settings.market_db))
    existing = existing_dates(con, table) if not args.force else set()
    con.close()

    sorted_dates = sorted(date_rows.keys())
    skip_count = 0
    write_count = 0
    total_rows_written = 0

    for d in sorted_dates:
        if d in existing:
            skip_count += 1
            continue
        rows = date_rows[d]
        n = write_market(settings, table, d, columns, rows)
        total_rows_written += n
        write_count += 1
        if write_count % 10 == 0:
            print(f"  写入进度: {write_count}/{len(sorted_dates)} dt={d}")

    # 统计
    print(f"\n=== 完成 ===")
    print(f"日期总数: {len(sorted_dates)}, 新写入: {write_count}, 跳过(已有): {skip_count}")
    print(f"总行数: {total_rows_written:,}")

    # 最终验证
    con = duckdb.connect(str(settings.market_db))
    cnt = con.execute(f"select count(*) from {table}").fetchone()[0]
    dr = con.execute(f"select min(trade_date), max(trade_date) from {table}").fetchone()
    sc = con.execute(f"select sector, count(*) as c from {table} group by sector order by c desc").fetchall()
    print(f"表 {table}: {cnt:,} 行, 日期 {dr[0]} ~ {dr[1]}")
    print(f"赛道分布:")
    for sector, c in sc:
        print(f"  {sector}: {c:,}")
    con.close()


if __name__ == "__main__":
    main()
