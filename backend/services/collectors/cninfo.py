"""巨潮资讯公告采集器:hisAnnouncement/query POST 表单逐股检索。
stock 参数先用纯 code,查不到再试 "code,orgId" 变体(orgId 按市场猜
gssh0/gssz0 前缀,不强求精确;实测 000001→gssz0000001 可查)。"""
from datetime import datetime, timedelta, timezone

import httpx

from . import _common as c
from . import register
from .base import DataSet

TIMEOUT = 20
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
      "X-Requested-With": "XMLHttpRequest",
      "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice"}
QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
STATIC_BASE = "http://static.cninfo.com.cn/"
COLUMNS = ["symbol", "ann_date", "title", "adjunct_url"]
_CST = timezone(timedelta(hours=8))  # 公告时间戳为北京时区当日零点,按 +8 取日期


def parse_announcements(payload) -> list[tuple]:
    """查询响应 → (ann_date, title, adjunct_url);announcementTime 兼容毫秒/字符串。"""
    anns = (payload or {}).get("announcements") or []
    rows = []
    for a in anns:
        if not isinstance(a, dict):
            continue
        ts = a.get("announcementTime")
        if isinstance(ts, (int, float)):  # epoch 毫秒
            ann_date = datetime.fromtimestamp(
                ts / 1000, tz=_CST).strftime("%Y-%m-%d")
        else:
            ann_date = str(ts or "")[:10] or None
        url = a.get("adjunctUrl")
        rows.append((ann_date, a.get("announcementTitle"),
                     (STATIC_BASE + str(url)) if url else None))
    return rows


def fetch_announcements(args, ctx):
    size = int((args or {}).get("page_size", 30))
    rows: list[tuple] = []
    for sym in c.iter_symbols(args):
        code = sym.split(".")[0]
        org = ("gssh0" if code.startswith("6") else "gssz0") + code
        column = "sse" if code.startswith("6") else "szse"
        got: list[tuple] = []
        for stock in (code, f"{code},{org}"):
            data = {"pageNum": 1, "pageSize": size, "column": column,
                    "tabName": "fulltext", "stock": stock, "searchkey": "",
                    "secid": "", "plate": "", "category": "", "trade": "",
                    "seDate": "", "sortName": "", "sortType": "",
                    "isHLtitle": "true"}
            r = httpx.post(QUERY_URL, data=data, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            got = parse_announcements(r.json())
            if got:
                break
        rows.extend((sym,) + t for t in got)
    return list(COLUMNS), rows


register(DataSet(
    key="cninfo.announcements", source="cninfo", name="公告检索",
    module="cninfo", desc="巨潮 hisAnnouncement/query 逐股最近公告(标题+PDF链接)",
    mode="per_symbol", requires=None,
    target_table="ods_cninfo_announcements", fetch=fetch_announcements))
