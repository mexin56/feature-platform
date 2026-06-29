"""东方财富采集器:push2 列表(clist)+ datacenter 报表 + 7x24 快讯 + 个股新闻。
parse_* 均为纯函数便于单测;datacenter 列名统一小写、缺 key 容错为 None,
"-" 等占位符落 None,"YYYY-MM-DD 00:00:00" 截为日期。"""
import json
import re

import httpx

from . import _common as c
from . import register
from .base import DataSet

TIMEOUT = 20
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
NEWS_LIST_URL = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
STOCK_NEWS_URL = "https://search-api-web.eastmoney.com/search/jsonp"
ALL_STOCK_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
PAGE_SIZE = 10000

BOARD_FIELDS = ["f12", "f14", "f3", "f104", "f105", "f128", "f140"]
BOARD_COLUMNS = ["code", "name", "pct_chg", "up_count", "down_count",
                 "lead_stock", "lead_stock_code"]
FLOW_FIELDS = ["f12", "f14", "f62", "f66", "f72", "f78", "f84", "f184"]
FLOW_COLUMNS = ["code", "name", "main_net_in", "super_net_in", "large_net_in",
                "mid_net_in", "small_net_in", "main_net_pct"]
NEWS_COLUMNS = ["news_code", "title", "digest", "show_time"]
STOCK_NEWS_COLUMNS = ["symbol", "title", "content", "pub_date", "media", "url"]

_DATE_TIME0_RE = re.compile(r"^\d{4}-\d{2}-\d{2} 00:00:00$")
_EM_TAG_RE = re.compile(r"</?em>")


def _reg(dataset: str, name: str, desc: str, mode: str, fetch) -> None:
    register(DataSet(
        key=f"eastmoney.{dataset}", source="eastmoney", name=name,
        module="eastmoney", desc=desc, mode=mode, requires=None,
        target_table=f"ods_eastmoney_{dataset}", fetch=fetch))


def _safe(v):
    if v in (None, "-", "--", ""):
        return None
    v = c.to_scalar(v)
    if isinstance(v, str) and _DATE_TIME0_RE.match(v):
        return v[:10]
    return v


def _get_json(url: str, params: dict | None = None) -> dict:
    r = httpx.get(url, params=params, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# ---------- 解析纯函数 ----------

def parse_clist(payload, fields) -> list[tuple]:
    """push2 clist 响应 → 按 fields 取值的行;diff 兼容 list/dict 两种形态。"""
    diff = ((payload or {}).get("data") or {}).get("diff") or []
    if isinstance(diff, dict):
        diff = list(diff.values())
    return [tuple(_safe(it.get(f)) for f in fields)
            for it in diff if isinstance(it, dict)]


def parse_datacenter(payload) -> tuple[list[str], list[tuple]]:
    """datacenter 响应 → (小写列名, 行);列取首行 key,后续行缺 key 补 None。"""
    data = ((payload or {}).get("result") or {}).get("data") or []
    data = [d for d in data if isinstance(d, dict)]
    if not data:
        return [], []
    keys = [str(k) for k in data[0]]
    rows = [tuple(_safe(d.get(k)) for k in keys) for d in data]
    return [k.lower() for k in keys], rows


def parse_news_list(payload) -> list[tuple]:
    """7x24 快讯列表 → (news_code, title, digest, show_time);digest 兼容 summary。"""
    items = ((payload or {}).get("data") or {}).get("fastNewsList") or []
    return [(str(it.get("code") or ""), _safe(it.get("title")),
             _safe(it.get("digest") or it.get("summary")), _safe(it.get("showTime")))
            for it in items if isinstance(it, dict)]


def parse_stock_news(text: str) -> list[tuple]:
    """search-api jsonp/json 文本 → (title, content, pub_date, media, url);
    去 <em> 高亮标签,结构不符返回空。"""
    s = text or ""
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return []
    try:
        payload = json.loads(s[i:j + 1])
    except ValueError:
        return []
    arts = ((payload.get("result") or {}).get("cmsArticleWebOld")) or []
    rows = []
    for a in arts:
        if not isinstance(a, dict):
            continue
        title = _EM_TAG_RE.sub("", str(a.get("title") or "")) or None
        content = _EM_TAG_RE.sub("", str(a.get("content") or "")) or None
        rows.append((title, content, _safe(a.get("date")),
                     _safe(a.get("mediaName")), _safe(a.get("url"))))
    return rows


# ---------- fetch ----------

def _clist_params(fs: str, fields: list[str], pn: int = 1, fid: str = "f3") -> dict:
    return {"pn": pn, "pz": PAGE_SIZE, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": fid, "fs": fs, "fields": ",".join(fields)}


def _board_fetch(fs: str, ths_type: str):
    """push2 clist 优先;push2 不可达(部分网络屏蔽 *.eastmoney.com push2 域名族)
    时回退 tushare 同花顺板块(ths_index 板块全集 + ths_daily 当日涨跌),列对齐 BOARD_COLUMNS。
    ths_type: 'I'=行业 'N'=概念(同花顺口径)。"""
    def fetch(args, ctx):
        try:
            payload = _get_json(CLIST_URL, _clist_params(fs, BOARD_FIELDS))
            rows = parse_clist(payload, BOARD_FIELDS)
            if rows:
                return list(BOARD_COLUMNS), rows
        except httpx.HTTPError:
            pass
        return list(BOARD_COLUMNS), _board_rows_tushare(ths_type, ctx)
    return fetch


def _board_rows_tushare(ths_type: str, ctx) -> list[tuple]:
    from .tushare_client import get_pro

    pro = get_pro(ctx.get("tushare_token") if isinstance(ctx, dict) else None)
    idx = pro.ths_index(exchange="A", type=ths_type)  # ts_code, name, count, ...
    dt_nodash = c.ctx_dt(ctx).replace("-", "")
    pct = {}
    try:
        daily = pro.ths_daily(trade_date=dt_nodash)  # ts_code, pct_change, ...
        if daily is not None and not daily.empty and "pct_change" in daily.columns:
            pct = dict(zip(daily["ts_code"], daily["pct_change"]))
    except Exception:  # noqa: BLE001  当日无行情(非交易日/盘中)时仅缺涨跌幅
        pass
    rows = []
    for ts_code, name in zip(idx["ts_code"], idx["name"]):
        v = pct.get(ts_code)
        rows.append((ts_code, name, None if v is None else float(v),
                     None, None, None, None))  # 同花顺口径无涨跌家数/领涨股
    return rows


def fetch_market_fund_flow_spot(args, ctx):
    rows: list[tuple] = []
    for pn in range(1, 6):  # pz=10000 正常一页拿全;封顶 5 页防接口异常死循环
        params = _clist_params(ALL_STOCK_FS, FLOW_FIELDS, pn, fid="f62")
        page = parse_clist(_get_json(CLIST_URL, params), FLOW_FIELDS)
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
    return list(FLOW_COLUMNS), rows


def _dc_fetch(report: str, filter_fn=None, sort: tuple | None = None,
              size: int = 500):
    def fetch(args, ctx):
        params = {"reportName": report, "columns": "ALL", "pageNumber": 1,
                  "pageSize": size, "source": "WEB", "client": "WEB"}
        if filter_fn:
            params["filter"] = filter_fn(c.ctx_dt(ctx))
        if sort:
            params["sortColumns"], params["sortTypes"] = sort
        return parse_datacenter(_get_json(DATACENTER_URL, params))
    return fetch


def fetch_global_news(args, ctx):
    payload = _get_json(NEWS_LIST_URL, {
        "client": "web", "biz": "web_724", "fastColumn": "102",
        "sortEnd": "", "pageSize": 50, "req_trace": ""})
    return list(NEWS_COLUMNS), parse_news_list(payload)


def fetch_stock_news(args, ctx):
    rows: list[tuple] = []
    for sym in c.iter_symbols(args):
        param = {"uid": "", "keyword": sym.split(".")[0],
                 "type": ["cmsArticleWebOld"], "client": "web",
                 "clientType": "web", "clientVersion": "curr",
                 "param": {"cmsArticleWebOld": {
                     "searchScope": "default", "sort": "default", "pageIndex": 1,
                     "pageSize": 20, "preTag": "<em>", "postTag": "</em>"}}}
        r = httpx.get(STOCK_NEWS_URL,
                      params={"cb": "", "param": json.dumps(param, ensure_ascii=False)},
                      headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        rows.extend((sym,) + t for t in parse_stock_news(r.text))
    return list(STOCK_NEWS_COLUMNS), rows


_reg("industry_boards", "行业板块列表",
     "push2 clist fs=m:90+t:2(push2 屏蔽时回退 tushare 同花顺行业板块)",
     "snapshot", _board_fetch("m:90+t:2", "I"))
_reg("concept_boards", "概念板块列表",
     "push2 clist fs=m:90+t:3(push2 屏蔽时回退 tushare 同花顺概念板块)",
     "snapshot", _board_fetch("m:90+t:3", "N"))
_reg("market_fund_flow_spot", "全市场个股资金流快照",
     "push2 clist 主力/超大/大/中/小单净流入与占比(分页)",
     "snapshot", fetch_market_fund_flow_spot)
_reg("lhb_daily", "龙虎榜当日明细",
     "datacenter RPT_DAILYBILLBOARD_DETAILSNEW(TRADE_DATE=dt)",
     "snapshot", _dc_fetch("RPT_DAILYBILLBOARD_DETAILSNEW",
                           lambda dt: f"(TRADE_DATE='{dt}')",
                           ("SECURITY_CODE", "1")))
_reg("margin_summary", "融资融券汇总",
     "datacenter RPTA_RZRQ_LSHJ 近 30 期两市汇总",
     "snapshot", _dc_fetch("RPTA_RZRQ_LSHJ", None, ("DIM_DATE", "-1"), size=30))
_reg("block_trade_daily", "大宗交易当日",
     "datacenter RPT_DATA_BLOCKTRADE(TRADE_DATE=dt)",
     "snapshot", _dc_fetch("RPT_DATA_BLOCKTRADE",
                           lambda dt: f"(TRADE_DATE='{dt}')",
                           ("SECURITY_CODE", "1")))
_reg("unlock_calendar", "解禁日历(未来90天)",
     "datacenter RPT_LIFT_STAGE FREE_DATE∈[dt, dt+90d]",
     "snapshot", _dc_fetch(
         "RPT_LIFT_STAGE",
         lambda dt: f"(FREE_DATE>='{dt}')(FREE_DATE<='{c.days_after(dt, 90)}')",
         ("FREE_DATE", "1")))
_reg("global_news", "7x24 全球快讯", "np-weblist 快讯最近 50 条",
     "snapshot", fetch_global_news)
_reg("stock_news", "个股新闻", "search-api 逐股最近 20 条新闻",
     "per_symbol", fetch_stock_news)


# ── 机构龙虎榜明细:含机构席位编码 + 净买卖金额 ──
_LHB_DETAIL_COLUMNS = [
    "trade_date", "ts_code", "name", "close", "change_pct", "turnover_rate",
    "billboard_buy_amt", "billboard_sell_amt", "billboard_net_amt",
    "buy_seat", "sell_seat", "buy_ratio", "sell_ratio",
    "accum_amount", "explain", "explanation",
]


def fetch_lhb_detail(args, ctx):
    """龙虎榜明细含机构席位(BUY_SEAT/SELL_SEAT)+净买卖额。"""
    dt = c.ctx_dt(ctx)
    payload = _get_json(DATACENTER_URL, {
        "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
        "columns": ",".join([
            "TRADE_DATE", "SECUCODE", "SECURITY_NAME_ABBR", "CLOSE_PRICE",
            "CHANGE_RATE", "TURNOVERRATE", "BILLBOARD_BUY_AMT",
            "BILLBOARD_SELL_AMT", "BILLBOARD_NET_AMT",
            "BUY_SEAT", "SELL_SEAT", "BUY_RATIO", "SELL_RATIO",
            "ACCUM_AMOUNT", "EXPLAIN", "EXPLANATION",
        ]),
        "pageNumber": 1, "pageSize": 500,
        "filter": f"(TRADE_DATE='{dt}')",
        "sortColumns": "SECURITY_CODE", "sortTypes": "1",
        "source": "WEB", "client": "WEB",
    })
    raw = parse_datacenter(payload)
    if not raw[0]:
        return list(_LHB_DETAIL_COLUMNS), []
    col_map = {c: i for i, c in enumerate(raw[0])}
    rows = []
    for r in raw[1]:
        rows.append((
            r[col_map.get("trade_date")] if "trade_date" in col_map else None,
            r[col_map.get("secucode")] if "secucode" in col_map else None,
            r[col_map.get("security_name_abbr")] if "security_name_abbr" in col_map else None,
            r[col_map.get("close_price")] if "close_price" in col_map else None,
            r[col_map.get("change_rate")] if "change_rate" in col_map else None,
            r[col_map.get("turnoverrate")] if "turnoverrate" in col_map else None,
            r[col_map.get("billboard_buy_amt")] if "billboard_buy_amt" in col_map else None,
            r[col_map.get("billboard_sell_amt")] if "billboard_sell_amt" in col_map else None,
            r[col_map.get("billboard_net_amt")] if "billboard_net_amt" in col_map else None,
            r[col_map.get("buy_seat")] if "buy_seat" in col_map else None,
            r[col_map.get("sell_seat")] if "sell_seat" in col_map else None,
            r[col_map.get("buy_ratio")] if "buy_ratio" in col_map else None,
            r[col_map.get("sell_ratio")] if "sell_ratio" in col_map else None,
            r[col_map.get("accum_amount")] if "accum_amount" in col_map else None,
            r[col_map.get("explain")] if "explain" in col_map else None,
            r[col_map.get("explanation")] if "explanation" in col_map else None,
        ))
    return list(_LHB_DETAIL_COLUMNS), rows


# ── 市场情绪指标:涨跌比/成交额/涨停跌停统计 ──
_MARKET_EMOTION_COLUMNS = [
    "trade_date", "sh_close", "sz_close", "total_amount_yi",
    "up_count", "down_count", "flat_count", "limit_up", "limit_down",
]


def fetch_market_emotion(args, ctx):
    """市场情绪综合指标: 腾讯指数(收盘价) + 乐咕市场赚钱效应(涨跌家数/涨停跌停)。"""
    dt = c.ctx_dt(ctx)

    sh_close = sz_close = total_amount = 0.0
    up = down = flat = limit_up = limit_down = 0

    # ── 腾讯指数: 收盘价 + 成交额 ──
    try:
        import httpx as _h
        resp = _h.get(
            "https://qt.gtimg.cn/q=sh000001,sz399001",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://qt.gtimg.cn"},
            timeout=10,
        )
        for line in resp.text.split("\n"):
            if "sh000001" in line:
                parts = line.split("~")
                if len(parts) > 4:
                    sh_close = c.to_scalar(parts[3]) or 0.0
                    total_amount = float(parts[37]) if parts[37] else 0.0  # 成交额(万元)
            elif "sz399001" in line:
                parts = line.split("~")
                if len(parts) > 4:
                    sz_close = c.to_scalar(parts[3]) or 0.0
    except Exception:
        pass

    # ── 乐咕市场赚钱效应: 涨跌家数/涨停跌停 ──
    try:
        import httpx as _h2
        resp2 = _h2.get(
            "https://legulegu.com/stockdata/market-activity",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
            timeout=10,
        )
        from bs4 import BeautifulSoup as _BS
        import pandas as _pd
        soup = _BS(resp2.text, "lxml")
        tables = soup.find_all("table")
        if tables:
            dfs = _pd.read_html(str(tables[0]))
            if dfs:
                df = dfs[0]
                # 第1行: 上涨/下跌/平盘
                if len(df) > 0:
                    up = int(df.iloc[0, 1]) if _pd.notna(df.iloc[0, 1]) else 0
                    down = int(df.iloc[0, 3]) if len(df.columns) > 3 and _pd.notna(df.iloc[0, 3]) else 0
                    flat = int(df.iloc[0, 5]) if len(df.columns) > 5 and _pd.notna(df.iloc[0, 5]) else 0
                # 第2行: 涨停/跌停
                if len(df) > 1:
                    limit_up = int(df.iloc[1, 1]) if _pd.notna(df.iloc[1, 1]) else 0
                    limit_down = int(df.iloc[1, 3]) if len(df.columns) > 3 and _pd.notna(df.iloc[1, 3]) else 0
    except Exception:
        pass

    # 成交额: 万元 → 亿
    total_yi = round(total_amount / 10_000, 2) if total_amount else 0.0

    return list(_MARKET_EMOTION_COLUMNS), [(
        dt, sh_close, sz_close, total_yi,
        up, down, flat, limit_up, limit_down,
    )]


# ── 龙虎榜机构明细(含席位编码+净买卖额) ──


_reg("lhb_detail", "龙虎榜机构明细",
     "datacenter 龙虎榜明细含机构席位(BUY_SEAT/SELL_SEAT)+净买卖额",
     "snapshot", fetch_lhb_detail)
_reg("market_emotion", "市场情绪指标",
     "push2 全市场涨跌比+成交额+涨停跌停统计(当日)",
     "snapshot", fetch_market_emotion)
