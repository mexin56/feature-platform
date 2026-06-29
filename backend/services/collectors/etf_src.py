"""QMT(xtquant) ETF 全市场数据采集器。

采集数据:
  1. etf_spot  — 实时行情快照(全市场)
  2. etf_daily — 日线行情(全市场,可带日频 OHLCV)
  3. etf_info  — 基础信息(代码/名称/成立日/到期日等)

赛道分类策略:
  SECTOR_KEYWORDS 基于 ETF 名称关键词匹配归类,
  每只 ETF 写入 sector 字段,下游可直接 group / 筛选。

需本机运行 QMT 终端(miniQMT 即可),xtquant 连接本地 127.0.0.1:58610。
"""
from . import _common as c
from . import register
from .base import DataSet

# ── 赛道关键词 ──────────────────────────────────────────
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
    """按 ETF 名称匹配赛道;无匹配返回'其他'。"""
    for sector, keywords in SECTOR_KEYWORDS:
        for kw in keywords:
            if kw in name:
                return sector
    return "其他"


# ── QMT 连接辅助 ────────────────────────────────────────
def _connect() -> None:
    """确保 xtdata 已连接;首次调用会自动连接本地 QMT 终端(约需 1-2s)。"""
    import xtquant.xtdata as xtd
    xtd.enable_hello = False


COLUMNS_SPOT = [
    "symbol", "name", "sector",
    "last_price", "open", "high", "low", "pre_close",
    "change_pct", "amount", "volume", "turnover",
    "amplitude",
]


def fetch_etf_spot(args: dict, ctx: dict) -> tuple[list[str], list[tuple]]:
    """全市场 ETF 实时行情快照(snapshot 模式)。

    返回字段:
      symbol, name, sector, last_price, open, high, low,
      pre_close, change_pct, amount, volume, turnover, amplitude
    """
    import xtquant.xtdata as xtd

    _connect()

    codes = xtd.get_stock_list_in_sector("沪深ETF")
    if not codes:
        return list(COLUMNS_SPOT), []

    # 批量获取详情(含名称)
    details = xtd.get_instrument_detail_list(codes)

    # 批量获取实时行情(分批次,避免单次参数过大)
    BATCH = 300
    all_ticks: dict = {}
    for i in range(0, len(codes), BATCH):
        batch = codes[i : i + BATCH]
        ticks = xtd.get_full_tick(batch)
        all_ticks.update(ticks)

    rows: list[tuple] = []
    for code in codes:
        q = all_ticks.get(code) or {}
        last_price = q.get("lastPrice", 0)
        last_close = q.get("lastClose", 0) or q.get("lastSettlementPrice", 0)

        # 过滤停牌 / 无行情
        if last_price <= 0:
            continue

        change_pct = round((last_price / last_close - 1) * 100, 2) if last_close > 0 else 0.0
        high = q.get("high", last_price)
        low = q.get("low", last_price)
        amplitude = (
            round((high - low) / last_close * 100, 2) if last_close > 0 else 0.0
        )

        # 名称解码
        raw = details.get(code, {})
        raw_name = raw.get("InstrumentName", code)
        try:
            name = raw_name.encode("latin1").decode("utf-8", errors="replace")
        except Exception:
            name = raw_name

        short = code.replace(".SH", "").replace(".SZ", "")
        sector = classify_etf(name)

        rows.append((
            short, name, sector,
            float(last_price),
            float(q.get("open", 0)),
            float(high),
            float(low),
            float(last_close),
            change_pct,
            float(q.get("amount", 0)),
            float(q.get("volume", 0)),
            float(q.get("pvolume", 0)),
            amplitude,
        ))

    return list(COLUMNS_SPOT), rows


register(DataSet(
    key="qmt.etf_spot",
    source="qmt",
    name="ETF 实时行情(全市场)",
    module="xtquant",
    desc="xtdata 全市场 ETF 行情快照(含赛道分类)",
    mode="snapshot",
    requires="package",
    target_table="ods_qmt_etf_spot",
    fetch=fetch_etf_spot,
))


# ── ETF 日线行情(逐只) ──────────────────────────────────
COLUMNS_DAILY = [
    "symbol", "name", "sector",
    "open", "high", "low", "close",
    "volume", "amount", "change_pct",
    "pre_close", "trade_date",
]


def _decode_name(raw_name: str) -> str:
    try:
        return raw_name.encode("latin1").decode("utf-8", errors="replace")
    except Exception:
        return raw_name


def fetch_etf_daily_one(sym: str, start: str, end: str) -> tuple:
    """单只 ETF 日线下载 + 本地读取。"""
    import xtquant.xtdata as xtd

    _connect()

    raw_code = sym
    if not raw_code.endswith(".SH") and not raw_code.endswith(".SZ"):
        raw_code = sym + (".SH" if sym.startswith("5") else ".SZ")

    xtd.download_history_data(raw_code, "1d", start, end)

    result = xtd.get_local_data(
        field_list=["open", "high", "low", "close", "volume", "amount"],
        stock_list=[raw_code],
        period="1d",
        start_time=start,
        end_time=end,
        count=-1,
        dividend_type="front",
    )
    if raw_code not in result or result[raw_code].empty:
        return None

    df = result[raw_code]
    if df.empty:
        return None

    # 获取名称、赛道
    detail = xtd.get_instrument_detail(raw_code)
    raw_name = (detail or {}).get("InstrumentName", sym)
    name = _decode_name(raw_name)
    sector = classify_etf(name)

    rows = []
    for idx in df.index:
        close_ = float(df.at[idx, "close"])
        open_ = float(df.at[idx, "open"])
        pre_close = float(df.at[idx, "pre_close"]
                          ) if "pre_close" in df.columns else close_
        change_pct = round((close_ / pre_close - 1) * 100, 2) if pre_close > 0 else 0.0

        rows.append((
            sym, name, sector,
            float(df.at[idx, "open"]) if "open" in df.columns else None,
            float(df.at[idx, "high"]) if "high" in df.columns else None,
            float(df.at[idx, "low"]) if "low" in df.columns else None,
            close_,
            int(df.at[idx, "volume"]) if "volume" in df.columns else None,
            float(df.at[idx, "amount"]) if "amount" in df.columns else None,
            change_pct,
            float(pre_close),
            str(idx.date()) if hasattr(idx, "date") else str(idx),
        ))

    return rows


def fetch_etf_daily(args: dict, ctx: dict) -> tuple[list[str], list[tuple]]:
    """逐只 ETF 日线行情(per_symbol 模式)。

    args 支持:
      - start_date: 起始日期 YYYY-MM-DD(默认 dt 前 120 天)
      - end_date:   结束日期 YYYY-MM-DD(默认 dt)
    """
    dt = c.ctx_dt(ctx)
    a = args or {}
    start = a.get("start_date", c.days_before(dt, 120))
    end = a.get("end_date", dt)
    start_n = start.replace("-", "")
    end_n = end.replace("-", "")

    import xtquant.xtdata as xtd
    _connect()
    codes = xtd.get_stock_list_in_sector("沪深ETF")
    if not codes:
        return list(COLUMNS_DAILY), []

    all_rows: list[tuple] = []
    for code in codes:
        try:
            rows = fetch_etf_daily_one(code, start_n, end_n)
            if rows:
                all_rows.extend(rows)
        except Exception:
            continue

    return list(COLUMNS_DAILY), all_rows


register(DataSet(
    key="qmt.etf_daily",
    source="qmt",
    name="ETF 日线行情(全市场)",
    module="xtquant",
    desc="xtdata 全市场 ETF 日线 OHLCV(含赛道分类),默认 dt 前 120 天",
    mode="snapshot",
    requires="package",
    target_table="ods_qmt_etf_daily",
    fetch=fetch_etf_daily,
))


# ── ETF 基础信息 ────────────────────────────────────────
COLUMNS_INFO = [
    "symbol", "name", "sector",
    "exchange", "product_type",
    "create_date", "open_date", "expire_date",
    "is_trading",
]


def fetch_etf_info(args: dict, ctx: dict) -> tuple[list[str], list[tuple]]:
    """全市场 ETF 基础信息快照(代码/名称/赛道/交易所/成立日等)。"""
    import xtquant.xtdata as xtd

    _connect()
    codes = xtd.get_stock_list_in_sector("沪深ETF")
    if not codes:
        return list(COLUMNS_INFO), []

    details = xtd.get_instrument_detail_list(codes)

    rows: list[tuple] = []
    for code in codes:
        raw = details.get(code, {})
        raw_name = raw.get("InstrumentName", code)
        name = _decode_name(raw_name)
        sector = classify_etf(name)

        rows.append((
            code.replace(".SH", "").replace(".SZ", ""),
            name,
            sector,
            raw.get("ExchangeID", ""),
            raw.get("ProductType", ""),
            str(raw.get("CreateDate", "")) if raw.get("CreateDate") else None,
            str(raw.get("OpenDate", "")) if raw.get("OpenDate") else None,
            str(raw.get("ExpireDate", "")) if raw.get("ExpireDate") else None,
            bool(raw.get("IsTrading", False)),
        ))

    return list(COLUMNS_INFO), rows


register(DataSet(
    key="qmt.etf_info",
    source="qmt",
    name="ETF 基础信息(全市场)",
    module="xtquant",
    desc="xtdata 全市场 ETF 基础信息(交易所/成立日/赛道分类)",
    mode="snapshot",
    requires="package",
    target_table="ods_qmt_etf_info",
    fetch=fetch_etf_info,
))
