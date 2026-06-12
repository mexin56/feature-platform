"""腾讯行情采集器(HTTP 参考实现):全市场实时快照 + 六大指数快照。
代码全集来自东方财富 push2 列表接口(一次调用拿全 A 股 code+market),
行情来自 qt.gtimg.cn 按 60 只分批拉取;GBK 解码,畸形条目容错跳过。"""
import httpx

from . import register
from .base import DataSet

TIMEOUT = 20
BATCH = 60
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_CODES_URL = ("http://82.push2.eastmoney.com/api/qt/clist/get"
              "?pn=1&pz=10000&po=1&np=1&fltt=2&invt=2&fid=f3"
              "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
              "&fields=f12,f13")
_QUOTE_URL = "https://qt.gtimg.cn/q="

SPOT_COLUMNS = ["code", "name", "price", "pre_close", "open", "volume_hand",
                "amount_wan", "high", "low", "pct_chg", "turnover_pct",
                "pe_ttm", "pb", "total_mcap_yi", "float_mcap_yi", "amplitude_pct"]
INDEX_COLUMNS = ["code", "name", "price", "change_pct", "volume", "amount"]
INDEX_CODES = ["sh000001", "sz399001", "sz399006", "sh000688", "sh000300", "sh000905"]


def _f(s) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _entries(text: str) -> list[list[str]]:
    """v_xxx=\"a~b~...\" 响应文本 → 各条目按 ~ 切分的字段列表。"""
    out = []
    for seg in text.split(";"):
        _, eq, body = seg.partition("=")
        if not eq:
            continue
        body = body.strip().strip('"')
        if body:
            out.append(body.split("~"))
    return out


def parse_spot(text: str) -> list[tuple]:
    """全市场快照解析(腾讯字段位:33/34 高低,36/37 量额,43 振幅,44/45 流通/总市值,46 PB)。"""
    rows = []
    for p in _entries(text):
        if len(p) < 47:  # 字段不足 = 畸形条目,跳过
            continue
        rows.append((p[2], p[1], _f(p[3]), _f(p[4]), _f(p[5]), _f(p[36]),
                     _f(p[37]), _f(p[33]), _f(p[34]), _f(p[32]), _f(p[38]),
                     _f(p[39]), _f(p[46]), _f(p[45]), _f(p[44]), _f(p[43])))
    return rows


def parse_index(text: str) -> list[tuple]:
    """指数快照解析:32 涨跌%,36 成交量(手),37 成交额(万)。"""
    rows = []
    for p in _entries(text):
        if len(p) < 38:
            continue
        rows.append((p[2], p[1], _f(p[3]), _f(p[32]), _f(p[36]), _f(p[37])))
    return rows


def _all_codes(client: httpx.Client) -> list[str]:
    """全 A 股代码全集(带 sz/sh 前缀);东财 push2 单次调用,失败则 tushare stock_basic 兜底
    (部分网络环境屏蔽 push2 全族域名)。"""
    try:
        r = client.get(_CODES_URL)
        r.raise_for_status()
        items = ((r.json().get("data") or {}).get("diff")) or []
        out = []
        for it in items:
            code = str(it.get("f12") or "")
            if len(code) == 6 and code.isdigit():
                out.append(("sh" if it.get("f13") == 1 else "sz") + code)
        if out:
            return out
    except Exception:  # noqa: BLE001  统一走兜底
        pass
    return _all_codes_tushare()


def _all_codes_tushare() -> list[str]:
    """兜底代码全集:tushare stock_basic(经专用网关,ts_code 形如 000001.SZ)。"""
    from .tushare_client import get_pro

    df = get_pro().stock_basic(list_status="L", fields="ts_code")
    out = []
    for ts_code in df["ts_code"]:
        code, mkt = str(ts_code).split(".")
        out.append(mkt.lower() + code)
    return out


def _get_quotes(client: httpx.Client, codes: list[str]) -> str:
    r = client.get(_QUOTE_URL + ",".join(codes))
    r.raise_for_status()
    return r.content.decode("gbk", errors="replace")


def fetch_spot(args: dict, ctx: dict) -> tuple[list[str], list[tuple]]:
    rows: list[tuple] = []
    with httpx.Client(timeout=TIMEOUT, headers=UA) as client:
        codes = _all_codes(client)
        for i in range(0, len(codes), BATCH):
            rows.extend(parse_spot(_get_quotes(client, codes[i:i + BATCH])))
    return SPOT_COLUMNS, rows


def fetch_index_spot(args: dict, ctx: dict) -> tuple[list[str], list[tuple]]:
    with httpx.Client(timeout=TIMEOUT, headers=UA) as client:
        return INDEX_COLUMNS, parse_index(_get_quotes(client, INDEX_CODES))


register(DataSet(
    key="tencent.spot", source="tencent", name="全市场实时行情快照",
    module="tencent", desc="腾讯 qt.gtimg.cn 全 A 股实时行情(60 只/批)",
    mode="snapshot", requires=None, target_table="ods_tencent_spot",
    fetch=fetch_spot))
register(DataSet(
    key="tencent.index_spot", source="tencent", name="六大指数快照",
    module="tencent", desc="上证/深成/创业板/科创50/沪深300/中证500 实时快照",
    mode="snapshot", requires=None, target_table="ods_tencent_index_spot",
    fetch=fetch_index_spot))
