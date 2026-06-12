"""新浪财报采集器:三大报表(利润 lrb/资产负债 fzb/现金流 llb)逐股。
统一落长表 (symbol, report_date, item, value)——以长表稳过新旧接口结构差异。
新接口(quotes.sina.cn openapi,JSON)失败或结构不符时,降级旧下载口
(money.finance.sina.com.cn vDOWN_*,GBK TSV)。"""
import re

import httpx

from . import _common as c
from . import register
from .base import DataSet

TIMEOUT = 20
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
API_URL = ("https://quotes.sina.cn/cn/api/openapi.php/"
           "CompanyFinanceService.getFinanceReport2022")
LEGACY_URL = ("http://money.finance.sina.com.cn/corp/go.php/vDOWN_{page}/"
              "displaytype/4/stockid/{code}/ctrl/all.phtml")
COLUMNS = ["symbol", "report_date", "item", "value"]
_LEGACY_PAGE = {"lrb": "ProfitStatement", "fzb": "BalanceSheet", "llb": "CashFlow"}
_SOURCE_NAME = {"lrb": "利润表", "fzb": "资产负债表", "llb": "现金流量表"}


def _reg(dataset: str, name: str, desc: str, fetch) -> None:
    register(DataSet(
        key=f"sina.{dataset}", source="sina", name=name, module="sina",
        desc=desc, mode="per_symbol", requires=None,
        target_table=f"ods_sina_{dataset}", fetch=fetch))


def _norm_date(s) -> str:
    s = str(s or "").strip()
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _val(v):
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "--", "-", "None", "null"):
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return s


def parse_finance_report(payload) -> list[tuple]:
    """openapi JSON → (report_date, item, value) 长表;结构异常返回 []。
    实测结构:result.data.report_list = {日期: {"data": [{item_title, item_value}]}}。"""
    data = ((payload or {}).get("result") or {}).get("data") or {}
    report_list = data.get("report_list") or {}
    rows: list[tuple] = []
    if not isinstance(report_list, dict):
        return rows
    for rd, blk in report_list.items():
        items = (blk or {}).get("data") if isinstance(blk, dict) else None
        if isinstance(items, list):
            for it in items:
                if not isinstance(it, dict):
                    continue
                k = it.get("item_title") or it.get("item_field") or it.get("item")
                if k is not None:
                    rows.append((_norm_date(rd), str(k), _val(it.get("item_value"))))
        elif isinstance(items, dict):  # 兜底:data 直接是 {item: value}
            rows.extend((_norm_date(rd), str(k), _val(v)) for k, v in items.items())
    return rows


def parse_finance_report_legacy(text: str) -> list[tuple]:
    """旧下载口 TSV(首行报表日期,余行 项目\\t值...)→ 长表;结构异常返回 []。"""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    head = lines[0].split("\t")
    dates = [_norm_date(x) for x in head[1:]]
    rows: list[tuple] = []
    for ln in lines[1:]:
        cells = ln.split("\t")
        item = cells[0].strip()
        if not item or item.startswith("单位"):
            continue
        for j, d in enumerate(dates):
            if d and j + 1 < len(cells):
                rows.append((d, item, _val(cells[j + 1])))
    return rows


def _paper_code(sym: str) -> str:
    code = sym.split(".")[0]
    if code.isdigit():
        return ("sh" if code.startswith("6") else "sz") + code
    return sym


def _fetch_report(source: str):
    def fetch(args, ctx):
        rows: list[tuple] = []
        for sym in c.iter_symbols(args):
            got: list[tuple] = []
            try:
                r = httpx.get(API_URL, params={
                    "paperCode": _paper_code(sym), "source": source, "type": 0,
                    "page": 1, "num": int((args or {}).get("num", 8))},
                    headers=UA, timeout=TIMEOUT)
                r.raise_for_status()
                got = parse_finance_report(r.json())
            except Exception:  # noqa: BLE001  新接口失败 → 旧接口降级
                got = []
            if not got:
                r = httpx.get(
                    LEGACY_URL.format(page=_LEGACY_PAGE[source],
                                      code=sym.split(".")[0]),
                    headers=UA, timeout=TIMEOUT)
                r.raise_for_status()
                got = parse_finance_report_legacy(
                    r.content.decode("gbk", errors="replace"))
            rows.extend((sym,) + t for t in got)
        return list(COLUMNS), rows
    return fetch


for _src, _name in _SOURCE_NAME.items():
    _reg(f"financial_report_{_src}", _name,
         f"openapi getFinanceReport2022(source={_src})长表落地,失败降级旧下载口",
         _fetch_report(_src))
