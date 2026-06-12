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


def _board_fetch(fs: str):
    def fetch(args, ctx):
        payload = _get_json(CLIST_URL, _clist_params(fs, BOARD_FIELDS))
        return list(BOARD_COLUMNS), parse_clist(payload, BOARD_FIELDS)
    return fetch


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


_reg("industry_boards", "行业板块列表", "push2 clist fs=m:90+t:2 板块行情快照",
     "snapshot", _board_fetch("m:90+t:2"))
_reg("concept_boards", "概念板块列表", "push2 clist fs=m:90+t:3 板块行情快照",
     "snapshot", _board_fetch("m:90+t:3"))
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
