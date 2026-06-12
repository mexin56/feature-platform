"""同花顺采集器:接口最脆弱,尽力实现 + 失败抛明确 RuntimeError。
- hot_theme:热点题材以开放热股榜(小时榜)口径落地(fuyao 网关,旧域名兜底);
- north_flow_minute:北向分时无稳定开放 JSON 接口,尽力请求 + 不可达即报错;
- eps_consensus:解析 basic.10jqka.com.cn worth.html 业绩预测表(正则,GBK 兼容)。"""
import re

import httpx

from . import _common as c
from . import register
from .base import DataSet

TIMEOUT = 20
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
      "Referer": "http://www.10jqka.com.cn/"}
HOT_STOCK_URLS = (
    "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"
    "?stock_type=a&type=hour&list_type=normal",
    "https://eq.10jqka.com.cn/open/api/hot_list/v1/hot_stock/a/hour/data.json",
)
NORTH_MINUTE_URL = "https://data.hexin.cn/market/hsgt/getHsgtMinuteData?type=north"
WORTH_URL = "http://basic.10jqka.com.cn/{code}/worth.html"

HOT_COLUMNS = ["rank", "code", "name", "hot_value", "tag"]
NORTH_COLUMNS = ["time", "north_net_in"]
EPS_COLUMNS = ["symbol", "period", "eps_avg", "institution_count"]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _reg(dataset: str, name: str, desc: str, mode: str, fetch) -> None:
    register(DataSet(
        key=f"ths.{dataset}", source="ths", name=name, module="ths",
        desc=desc, mode=mode, requires=None,
        target_table=f"ods_ths_{dataset}", fetch=fetch))


# ---------- 解析纯函数 ----------

def parse_hot_stock(payload) -> list[tuple]:
    """热股榜 JSON → (rank, code, name, hot_value, tag);tag 取 concept_tag 拼接。"""
    items = ((payload or {}).get("data") or {}).get("stock_list") or []
    rows = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        tag = it.get("tag")
        if isinstance(tag, dict):
            parts = tag.get("concept_tag") or []
            tag = ",".join(str(p) for p in parts) if isinstance(parts, list) else str(parts)
        elif tag is not None:
            tag = str(tag)
        rows.append((int(it.get("order") or i + 1), str(it.get("code") or ""),
                     it.get("name"), _f(it.get("rate") or it.get("hot_value")), tag))
    return rows


def parse_north_minute(payload) -> list[tuple]:
    """北向分时 JSON → (time, north_net_in);兼容 [[t, v], ...] 与 dict 两种条目。"""
    data = (payload or {}).get("data") or {}
    items = data.get("items") or data.get("list") or []
    rows = []
    for it in items:
        if isinstance(it, (list, tuple)) and len(it) >= 2:
            rows.append((str(it[0]), _f(it[1])))
        elif isinstance(it, dict):
            rows.append((str(it.get("time") or ""),
                         _f(it.get("value") if "value" in it else it.get("north"))))
    return rows


def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def parse_eps_html(html: str) -> list[tuple]:
    """worth.html 业绩预测表 → (period, eps_avg, institution_count)。
    取含「每股收益」或「预测年度」的首个表格;表头定位「均值」「机构」列。"""
    for tbl in re.findall(r"<table[^>]*>(.*?)</table>", html or "", re.S | re.I):
        if "每股收益" not in tbl and "预测年度" not in tbl:
            continue
        header, data = None, []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, re.S | re.I):
            cells = [_strip_tags(x) for x in
                     re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
            if not cells:
                continue
            if header is None and any(("机构" in x or "均值" in x) for x in cells):
                header = cells
            elif re.match(r"^\d{4}", cells[0]) and len(cells) >= 2:
                data.append(cells)
        if not data:
            continue

        def _col(needle, default):
            if header:
                for i, h in enumerate(header):
                    if needle in h:
                        return i
            return default

        i_eps, i_cnt = _col("均值", 1), _col("机构", None)
        rows = []
        for cells in data:
            eps = _f(cells[i_eps]) if i_eps < len(cells) else None
            cnt = None
            if i_cnt is not None and i_cnt < len(cells) and cells[i_cnt].isdigit():
                cnt = int(cells[i_cnt])
            rows.append((cells[0], eps, cnt))
        return rows
    return []


# ---------- fetch ----------

def fetch_hot_theme(args, ctx):
    err = "无候选地址"
    for url in HOT_STOCK_URLS:
        try:
            r = httpx.get(url, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            rows = parse_hot_stock(r.json())
            if rows:
                return list(HOT_COLUMNS), rows
            err = f"{url} 返回结构不含 stock_list"
        except Exception as e:  # noqa: BLE001
            err = f"{url}: {e}"
    raise RuntimeError(f"同花顺热股榜接口不可达/结构不符: {err}")


def fetch_north_flow_minute(args, ctx):
    try:
        r = httpx.get(NORTH_MINUTE_URL, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        rows = parse_north_minute(r.json())
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"同花顺北向分时接口不可达(无稳定开放接口,建议改用东财口径): {e}") from e
    if not rows:
        raise RuntimeError("同花顺北向分时返回结构不符(未解析到分时点)")
    return list(NORTH_COLUMNS), rows


def fetch_eps_consensus(args, ctx):
    rows: list[tuple] = []
    for sym in c.iter_symbols(args):
        code = sym.split(".")[0]
        try:
            r = httpx.get(WORTH_URL.format(code=code), headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"同花顺一致预期页请求失败({sym}): {e}") from e
        parsed = parse_eps_html(r.text) or parse_eps_html(
            r.content.decode("gbk", errors="replace"))
        if not parsed:
            raise RuntimeError(f"同花顺一致预期页解析失败({sym}): 未找到业绩预测表")
        rows.extend((sym,) + t for t in parsed)
    return list(EPS_COLUMNS), rows


_reg("hot_theme", "热点题材(热股榜)",
     "开放热股榜小时榜替代热点题材口径(fuyao 网关,旧域名兜底)",
     "snapshot", fetch_hot_theme)
_reg("north_flow_minute", "北向资金分时",
     "尽力实现:无稳定开放 JSON 接口,不可达时明确报错",
     "snapshot", fetch_north_flow_minute)
_reg("eps_consensus", "一致预期(业绩预测)",
     "basic.10jqka worth.html 业绩预测表解析,逐股",
     "per_symbol", fetch_eps_consensus)
