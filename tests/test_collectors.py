"""Phase5 T2:八源采集器(解析纯函数/逐股契约/库 mock 转换/目录完备性)。
全部 mock(httpx.get/post 与库入口 monkeypatch),不触网。"""
import re
import sys
import time
import types
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from backend.config import Settings
from backend.services.collectors import (CATALOG, available, cninfo, eastmoney,
                                         sina, ths, tushare_src)
from backend.services.collectors import _common
from backend.services.collectors.writer import write_market
from backend.services.templating import build_context

CTX = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))
DT = "2026-06-12"  # data_interval_end 的日期 = 采集 dt


class FakeResp:
    def __init__(self, json_data=None, text="", content=None, status=200):
        self._json = json_data
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")
        self.status_code = status

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture()
def sleeps(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: calls.append(s))
    return calls


def _fake_module(name: str, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# ---------- 目录完备性 ----------

def test_catalog_completeness():
    assert len(CATALOG) >= 55
    sources = {ds.source for ds in CATALOG.values()}
    assert sources >= {"tencent", "sina", "eastmoney", "ths", "cninfo",
                       "akshare", "baostock", "mootdx", "tushare", "qmt"}
    key_re = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
    for key, ds in CATALOG.items():
        assert key_re.match(key), key
        assert key.split(".", 1)[0] == ds.source, key
        assert ds.target_table == "ods_" + key.replace(".", "_"), key
        assert ds.mode in ("snapshot", "per_symbol"), key


def test_qmt_entries_unavailable_with_reason():
    qmt = [ds for ds in CATALOG.values() if ds.source == "qmt"]
    assert len(qmt) >= 8
    for ds in qmt:
        assert ds.fetch is None and ds.requires == "terminal"
        ok, reason = available(ds)
        assert not ok and "QMT" in reason


def test_per_symbol_requires_symbols():
    for key in ("akshare.stock_zh_a_hist", "sina.financial_report_lrb",
                "cninfo.announcements", "tushare.fina_indicator"):
        with pytest.raises(RuntimeError, match="symbols"):
            CATALOG[key].fetch({}, CTX)


# ---------- _common ----------

def test_common_df_to_table_nan_columns_and_scalars():
    df = pd.DataFrame({"代码": ["000001"], "涨跌幅-3日": [np.nan],
                       "a b": [pd.Timestamp("2026-06-12")], "n": [np.int64(5)]})
    cols, rows = _common.df_to_table(df)
    assert cols == ["代码", "涨跌幅_3日", "a_b", "n"]
    assert rows == [("000001", None, "2026-06-12", 5)]
    assert isinstance(rows[0][3], int)


def test_common_prev_quarter():
    assert _common.prev_quarter("2026-06-12") == (2026, 1)
    assert _common.prev_quarter("2026-02-01") == (2025, 4)


def test_writer_accepts_chinese_columns(tmp_path):
    import duckdb

    s = Settings(storage_dir=str(tmp_path))
    s.ensure_dirs()
    assert write_market(s, "ods_akshare_x", DT, ["代码", "最新价"],
                        [("000001", 1.5)]) == 1
    con = duckdb.connect(str(s.market_db), read_only=True)
    try:
        assert con.execute('select "最新价" from ods_akshare_x').fetchone()[0] == 1.5
    finally:
        con.close()


# ---------- eastmoney ----------

def test_em_parse_clist_tolerant():
    payload = {"data": {"diff": [
        {"f12": "BK0475", "f14": "银行", "f3": 1.2, "f104": 30, "f105": 12,
         "f128": "招商银行", "f140": "600036"},
        {"f12": "BK0451", "f14": "保险", "f3": "-"},
        "garbage"]}}
    rows = eastmoney.parse_clist(payload, eastmoney.BOARD_FIELDS)
    assert rows[0] == ("BK0475", "银行", 1.2, 30, 12, "招商银行", "600036")
    assert rows[1] == ("BK0451", "保险", None, None, None, None, None)
    assert len(rows) == 2
    assert eastmoney.parse_clist({}, eastmoney.BOARD_FIELDS) == []


def test_em_parse_datacenter():
    payload = {"result": {"data": [
        {"SECURITY_CODE": "000001", "TRADE_DATE": "2026-06-12 00:00:00",
         "NET_BUY": 1.5},
        {"SECURITY_CODE": "600519"}]}}
    cols, rows = eastmoney.parse_datacenter(payload)
    assert cols == ["security_code", "trade_date", "net_buy"]
    assert rows == [("000001", "2026-06-12", 1.5), ("600519", None, None)]
    assert eastmoney.parse_datacenter({"result": {"data": []}}) == ([], [])


def test_em_parse_news_list_summary_compat():
    payload = {"data": {"fastNewsList": [
        {"code": "20260612x", "title": "标题", "summary": "摘要",
         "showTime": "2026-06-12 23:55:35"}]}}
    assert eastmoney.parse_news_list(payload) == [
        ("20260612x", "标题", "摘要", "2026-06-12 23:55:35")]


def test_em_parse_stock_news_strips_em_tags():
    text = (' jsonp_cb({"result": {"cmsArticleWebOld": ['
            '{"title": "<em>平安银行</em>发布公告", "content": "内容<em>X</em>",'
            '"date": "2026-06-12 08:00:00", "mediaName": "证券时报",'
            '"url": "http://e/1"}]}})')
    rows = eastmoney.parse_stock_news(text)
    assert rows == [("平安银行发布公告", "内容X", "2026-06-12 08:00:00",
                     "证券时报", "http://e/1")]
    assert eastmoney.parse_stock_news("not json") == []


def test_em_board_snapshot_fetch(monkeypatch):
    import httpx

    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None, **kw):
        seen["url"], seen["params"] = url, params
        return FakeResp(json_data={"data": {"diff": [
            {"f12": "BK0475", "f14": "银行", "f3": 1.2}]}})

    monkeypatch.setattr(httpx, "get", fake_get)
    cols, rows = CATALOG["eastmoney.industry_boards"].fetch({}, CTX)
    assert cols == eastmoney.BOARD_COLUMNS
    assert rows[0][:2] == ("BK0475", "银行")
    assert "m:90+t:2" in seen["params"]["fs"]


def test_em_lhb_filter_uses_ctx_dt(monkeypatch):
    import httpx

    seen = {}

    def fake_get(url, params=None, **kw):
        seen["params"] = params
        return FakeResp(json_data={"result": {"data": [{"SECURITY_CODE": "1"}]}})

    monkeypatch.setattr(httpx, "get", fake_get)
    cols, rows = CATALOG["eastmoney.lhb_daily"].fetch({}, CTX)
    assert seen["params"]["filter"] == f"(TRADE_DATE='{DT}')"
    assert cols == ["security_code"] and rows == [("1",)]


def test_em_stock_news_per_symbol(monkeypatch, sleeps):
    import httpx

    keywords = []

    def fake_get(url, params=None, **kw):
        import json as _json

        keywords.append(_json.loads(params["param"])["keyword"])
        return FakeResp(text='{"result": {"cmsArticleWebOld": ['
                             '{"title": "T", "content": "C", "date": "2026-06-12",'
                             '"mediaName": "M", "url": "u"}]}}')

    monkeypatch.setattr(httpx, "get", fake_get)
    cols, rows = CATALOG["eastmoney.stock_news"].fetch(
        {"symbols": ["000001", "600519.SH"], "interval_sec": 0.2}, CTX)
    assert cols == eastmoney.STOCK_NEWS_COLUMNS
    assert keywords == ["000001", "600519"]  # 去掉 .SH 后缀
    assert [r[0] for r in rows] == ["000001", "600519.SH"]
    assert sleeps == [0.2]


# ---------- ths ----------

def test_ths_parse_hot_stock():
    payload = {"data": {"stock_list": [
        {"order": 1, "code": "001696", "name": "宗申动力", "rate": "462481.0",
         "tag": {"concept_tag": ["农机", "军民融合"], "popularity_tag": "5天4板"}},
        {"code": "300059", "name": "东方财富", "rate": None}]}}
    rows = ths.parse_hot_stock(payload)
    assert rows[0] == (1, "001696", "宗申动力", 462481.0, "农机,军民融合")
    assert rows[1] == (2, "300059", "东方财富", None, None)


def test_ths_hot_theme_fetch_with_fallback(monkeypatch):
    import httpx

    calls = []

    def fake_get(url, headers=None, timeout=None, **kw):
        calls.append(url)
        if len(calls) == 1:  # 第一个地址 404 → 落第二个
            return FakeResp(status=404)
        return FakeResp(json_data={"data": {"stock_list": [
            {"order": 1, "code": "1", "name": "n", "rate": "2.0"}]}})

    monkeypatch.setattr(httpx, "get", fake_get)
    cols, rows = CATALOG["ths.hot_theme"].fetch({}, CTX)
    assert cols == ths.HOT_COLUMNS and len(rows) == 1 and len(calls) == 2


def test_ths_north_minute_unreachable_raises(monkeypatch):
    import httpx

    def fake_get(*a, **kw):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "get", fake_get)
    with pytest.raises(RuntimeError, match="北向分时"):
        CATALOG["ths.north_flow_minute"].fetch({}, CTX)


def test_ths_parse_north_minute():
    payload = {"data": {"items": [["09:30", "12.5"], {"time": "09:31", "value": 13}]}}
    assert ths.parse_north_minute(payload) == [("09:30", 12.5), ("09:31", 13.0)]


_EPS_HTML = """
<div><table>
<tr><th>预测年度</th><th>预测机构数</th><th>最小值</th><th>均值</th><th>最大值</th></tr>
<tr><td>2026</td><td>12</td><td>1.10</td><td>1.25</td><td>1.40</td></tr>
<tr><td>2027</td><td>10</td><td>1.30</td><td>1.45</td><td>1.60</td></tr>
</table>
<p>每股收益预测</p></div>
"""


def test_ths_parse_eps_html():
    rows = ths.parse_eps_html(_EPS_HTML.replace("预测年度", "预测年度(每股收益)"))
    assert rows == [("2026", 1.25, 12), ("2027", 1.45, 10)]
    assert ths.parse_eps_html("<html>无表格</html>") == []


def test_ths_eps_consensus_per_symbol(monkeypatch, sleeps):
    import httpx

    html = _EPS_HTML.replace("预测年度", "预测年度(每股收益)")
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp(text=html))
    cols, rows = CATALOG["ths.eps_consensus"].fetch({"symbols": ["000001"]}, CTX)
    assert cols == ths.EPS_COLUMNS
    assert rows[0] == ("000001", "2026", 1.25, 12)


# ---------- sina ----------

def test_sina_parse_finance_report():
    payload = {"result": {"data": {"report_list": {"20260331": {"data": [
        {"item_field": "BIZINCO", "item_title": "营业收入",
         "item_value": "35277000000.000000"},
        {"item_title": "净利息收入", "item_value": None}]}}}}}
    rows = sina.parse_finance_report(payload)
    assert rows == [("2026-03-31", "营业收入", 35277000000.0),
                    ("2026-03-31", "净利息收入", None)]
    assert sina.parse_finance_report({}) == []


def test_sina_parse_finance_report_legacy():
    text = "报表日期\t20260331\t20251231\n营业收入\t100\t90\n单位:元\t\t\n净利润\t10\t--\n"
    rows = sina.parse_finance_report_legacy(text)
    assert ("2026-03-31", "营业收入", 100.0) in rows
    assert ("2025-12-31", "净利润", None) in rows
    assert all(not r[1].startswith("单位") for r in rows)


def test_sina_fetch_falls_back_to_legacy(monkeypatch, sleeps):
    import httpx

    urls = []

    def fake_get(url, params=None, headers=None, timeout=None, **kw):
        urls.append(url)
        if "openapi" in url:  # 新接口结构不符 → 触发降级
            assert params["paperCode"] == "sz000001"
            return FakeResp(json_data={"result": {"data": {"report_list": {}}}})
        assert "vDOWN_ProfitStatement" in url and "000001" in url
        return FakeResp(content="报表日期\t20260331\n营业收入\t100\n".encode("gbk"))

    monkeypatch.setattr(httpx, "get", fake_get)
    cols, rows = CATALOG["sina.financial_report_lrb"].fetch(
        {"symbols": ["000001"]}, CTX)
    assert cols == sina.COLUMNS
    assert rows == [("000001", "2026-03-31", "营业收入", 100.0)]
    assert len(urls) == 2


def test_sina_fetch_new_api_ok(monkeypatch, sleeps):
    import httpx

    payload = {"result": {"data": {"report_list": {"20260331": {"data": [
        {"item_title": "营业收入", "item_value": "1.5"}]}}}}}
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp(json_data=payload))
    cols, rows = CATALOG["sina.financial_report_llb"].fetch(
        {"symbols": ["600519", "000001"], "interval_sec": 0.1}, CTX)
    assert [r[0] for r in rows] == ["600519", "000001"]
    assert sleeps == [0.1]


# ---------- cninfo ----------

def test_cninfo_parse_announcements():
    payload = {"announcements": [
        {"announcementTitle": "权益分派实施公告",
         "adjunctUrl": "finalpage/2026-06-05/1225352449.PDF",
         "announcementTime": 1780588800000},
        {"announcementTitle": "无附件", "announcementTime": "2026-06-04 10:00:00"}]}
    rows = cninfo.parse_announcements(payload)
    assert rows[0] == ("2026-06-05", "权益分派实施公告",
                       "http://static.cninfo.com.cn/finalpage/2026-06-05/1225352449.PDF")
    assert rows[1] == ("2026-06-04", "无附件", None)
    assert cninfo.parse_announcements({"announcements": None}) == []


def test_cninfo_fetch_org_id_fallback(monkeypatch, sleeps):
    import httpx

    stocks = []

    def fake_post(url, data=None, headers=None, timeout=None, **kw):
        stocks.append(data["stock"])
        if "," not in data["stock"]:  # 纯 code 查不到 → code,orgId 变体
            return FakeResp(json_data={"announcements": None})
        return FakeResp(json_data={"announcements": [
            {"announcementTitle": "T", "adjunctUrl": "a.PDF",
             "announcementTime": 1780588800000}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    cols, rows = CATALOG["cninfo.announcements"].fetch({"symbols": ["000001"]}, CTX)
    assert cols == cninfo.COLUMNS
    assert stocks == ["000001", "000001,gssz0000001"]
    assert rows == [("000001", "2026-06-05", "T",
                     "http://static.cninfo.com.cn/a.PDF")]


# ---------- akshare ----------

def test_akshare_snapshot_conversion(monkeypatch):
    df = pd.DataFrame({"代码": ["000001", "600519"], "最新价": [10.5, np.nan],
                       "今日主力净流入-净额": [1.0, 2.0]})
    mod = _fake_module("akshare", stock_zh_a_spot_em=lambda: df)
    monkeypatch.setitem(sys.modules, "akshare", mod)
    cols, rows = CATALOG["akshare.stock_zh_a_spot_em"].fetch({}, CTX)
    assert cols == ["代码", "最新价", "今日主力净流入_净额"]
    assert rows[1] == ("600519", None, 2.0)


def test_akshare_zt_pool_date_kwarg(monkeypatch):
    seen = {}

    def zt(date):
        seen["date"] = date
        return pd.DataFrame({"代码": ["000001"]})

    monkeypatch.setitem(sys.modules, "akshare",
                        _fake_module("akshare", stock_zt_pool_em=zt))
    CATALOG["akshare.stock_zt_pool_em"].fetch({}, CTX)
    assert seen["date"] == "20260612"


def test_akshare_market_activity_variant_fallback(monkeypatch):
    mod = _fake_module("akshare", stock_market_activity_em=lambda: pd.DataFrame(
        {"item": ["上涨"], "value": [3000]}))  # 无 legu 变体 → 落 em 变体
    monkeypatch.setitem(sys.modules, "akshare", mod)
    cols, rows = CATALOG["akshare.stock_market_activity_legu"].fetch({}, CTX)
    assert rows == [("上涨", 3000)]


def test_akshare_failure_reason_visible(monkeypatch):
    def boom():
        raise ValueError("接口维护中")

    monkeypatch.setitem(sys.modules, "akshare",
                        _fake_module("akshare", stock_hot_rank_em=boom))
    with pytest.raises(RuntimeError, match="stock_hot_rank_em.*接口维护中"):
        CATALOG["akshare.stock_hot_rank_em"].fetch({}, CTX)


def test_akshare_hist_per_symbol_contract(monkeypatch, sleeps):
    calls = []

    def hist(symbol, period, start_date, end_date, adjust):
        calls.append((symbol, period, start_date, end_date, adjust))
        return pd.DataFrame({"日期": ["2026-06-11"], "收盘": [10.0 if symbol == "000001" else np.nan]})

    monkeypatch.setitem(sys.modules, "akshare",
                        _fake_module("akshare", stock_zh_a_hist=hist))
    cols, rows = CATALOG["akshare.stock_zh_a_hist"].fetch(
        {"symbols": ["000001", "600519"], "interval_sec": 0.05}, CTX)
    assert calls == [("000001", "daily", "20260513", "20260612", "qfq"),
                     ("600519", "daily", "20260513", "20260612", "qfq")]
    assert cols == ["symbol", "日期", "收盘"]
    assert rows == [("000001", "2026-06-11", 10.0), ("600519", "2026-06-11", None)]
    assert sleeps == [0.05]  # interval_sec 生效(2 股 1 次间隔)


def test_akshare_individual_fund_flow_market(monkeypatch, sleeps):
    seen = []

    def flow(stock, market):
        seen.append((stock, market))
        return pd.DataFrame({"日期": ["2026-06-11"]})

    monkeypatch.setitem(sys.modules, "akshare",
                        _fake_module("akshare", stock_individual_fund_flow=flow))
    CATALOG["akshare.stock_individual_fund_flow"].fetch(
        {"symbols": ["600519", "000001", "830001"]}, CTX)
    assert seen == [("600519", "sh"), ("000001", "sz"), ("830001", "bj")]
    assert sleeps == [0.5, 0.5]  # 默认 interval 0.5s


# ---------- baostock ----------

class FakeRS:
    def __init__(self, fields, data, error_code="0", error_msg=""):
        self.fields = list(fields)
        self.error_code = error_code
        self.error_msg = error_msg
        self._data = list(data)
        self._i = -1

    def next(self):
        self._i += 1
        return self._i < len(self._data)

    def get_row_data(self):
        return list(self._data[self._i])


def _fake_bs(monkeypatch, **queries):
    state = {"login": 0, "logout": 0}
    mod = _fake_module(
        "baostock",
        login=lambda: state.__setitem__("login", state["login"] + 1) or
        types.SimpleNamespace(error_code="0", error_msg=""),
        logout=lambda: state.__setitem__("logout", state["logout"] + 1),
        **queries)
    monkeypatch.setitem(sys.modules, "baostock", mod)
    return state


def test_baostock_k_daily_contract(monkeypatch, sleeps):
    from backend.services.collectors import baostock_src

    calls = []
    width = len(baostock_src.K_FIELDS["d"].split(","))

    def k(code, fields, start_date, end_date, frequency, adjustflag):
        calls.append((code, frequency, adjustflag, start_date, end_date))
        row = ["2026-06-11", code, "10.0"] + [""] * (width - 3)
        return FakeRS(fields.split(","), [row])

    state = _fake_bs(monkeypatch, query_history_k_data_plus=k)
    cols, rows = CATALOG["baostock.history_k_daily"].fetch(
        {"symbols": ["600519", "000001"], "interval_sec": 0.01}, CTX)
    assert [c[0] for c in calls] == ["sh.600519", "sz.000001"]  # 前缀推导
    assert calls[0][1:] == ("d", "2", "2026-05-13", "2026-06-12")
    assert cols[:3] == ["symbol", "date", "code"]
    assert rows[0][:4] == ("600519", "2026-06-11", "sh.600519", "10.0")
    assert rows[0][-1] is None  # 空串 → None
    assert state == {"login": 1, "logout": 1}
    assert sleeps == [0.01]


def test_baostock_quarter_default_prev_quarter(monkeypatch, sleeps):
    seen = {}

    def profit(code, year, quarter):
        seen.update(code=code, year=year, quarter=quarter)
        return FakeRS(["code", "statDate", "roeAvg"],
                      [[code, "2026-03-31", "0.05"]])

    state = _fake_bs(monkeypatch, query_profit_data=profit)
    cols, rows = CATALOG["baostock.profit"].fetch({"symbols": ["000001"]}, CTX)
    assert seen == {"code": "sz.000001", "year": 2026, "quarter": 1}
    assert cols == ["symbol", "code", "statDate", "roeAvg"]
    assert rows == [("000001", "sz.000001", "2026-03-31", "0.05")]
    assert state == {"login": 1, "logout": 1}


def test_baostock_code_suffix_normalized(monkeypatch, sleeps):
    """交易所后缀写法(XXXXXX.SH/.sz,大小写均可)归一化为 baostock 的 sh./sz. 前缀。"""
    from backend.services.collectors import baostock_src

    calls = []

    def k(code, fields, start_date, end_date, frequency, adjustflag):
        calls.append(code)
        return FakeRS(fields.split(","), [])

    _fake_bs(monkeypatch, query_history_k_data_plus=k)
    CATALOG["baostock.history_k_daily"].fetch(
        {"symbols": ["600519.SH", "000001.sz", "sh.600000", "300750"],
         "interval_sec": 0}, CTX)
    assert calls == ["sh.600519", "sz.000001", "sh.600000", "sz.300750"]
    assert baostock_src.bs_code("688981.Sh") == "sh.688981"


def test_baostock_error_code_raises(monkeypatch):
    _fake_bs(monkeypatch, query_history_k_data_plus=lambda *a, **kw: FakeRS(
        [], [], error_code="10001", error_msg="网络超时"))
    with pytest.raises(RuntimeError, match="网络超时"):
        CATALOG["baostock.history_k_daily"].fetch({"symbols": ["000001"]}, CTX)


# ---------- tushare ----------

class FakePro:
    def __init__(self, df_factory):
        self.calls = []
        self._factory = df_factory

    def __getattr__(self, name):
        def call(**kw):
            self.calls.append((name, kw))
            return self._factory(name, kw)
        return call


def test_tushare_snapshot_trade_date(monkeypatch):
    pro = FakePro(lambda n, kw: pd.DataFrame(
        {"ts_code": ["000001.SZ"], "close": [10.5], "pe": [np.nan]}))
    monkeypatch.setattr(tushare_src, "get_pro", lambda *a, **kw: pro)
    cols, rows = CATALOG["tushare.daily"].fetch({}, CTX)
    assert pro.calls == [("daily", {"trade_date": "20260612"})]
    assert cols == ["ts_code", "close", "pe"]
    assert rows == [("000001.SZ", 10.5, None)]  # NaN → None


def test_tushare_limit_list_falls_back(monkeypatch):
    def factory(name, kw):
        if name == "limit_list_d":
            raise ValueError("api 已下线")
        return pd.DataFrame({"ts_code": ["000001.SZ"]})

    pro = FakePro(factory)
    monkeypatch.setattr(tushare_src, "get_pro", lambda *a, **kw: pro)
    cols, rows = CATALOG["tushare.limit_list"].fetch({}, CTX)
    assert [c[0] for c in pro.calls] == ["limit_list_d", "limit_list"]
    assert rows == [("000001.SZ",)]


def test_tushare_hs_const_two_calls(monkeypatch):
    pro = FakePro(lambda n, kw: pd.DataFrame({"ts_code": [kw["hs_type"]]}))
    monkeypatch.setattr(tushare_src, "get_pro", lambda *a, **kw: pro)
    cols, rows = CATALOG["tushare.hs_const"].fetch({}, CTX)
    assert [c[1]["hs_type"] for c in pro.calls] == ["SH", "SZ"]
    assert rows == [("SH",), ("SZ",)]


def test_tushare_fina_indicator_per_symbol(monkeypatch, sleeps):
    pro = FakePro(lambda n, kw: pd.DataFrame(
        {"ts_code": [kw["ts_code"]], "roe": [0.15]}))
    monkeypatch.setattr(tushare_src, "get_pro", lambda *a, **kw: pro)
    cols, rows = CATALOG["tushare.fina_indicator"].fetch(
        {"symbols": ["000001.SZ", "600519.SH"], "interval_sec": 0.02}, CTX)
    assert pro.calls == [("fina_indicator", {"ts_code": "000001.SZ", "limit": 8}),
                         ("fina_indicator", {"ts_code": "600519.SH", "limit": 8})]
    assert cols == ["symbol", "ts_code", "roe"]
    assert [r[0] for r in rows] == ["000001.SZ", "600519.SH"]
    assert sleeps == [0.02]


def test_tushare_ts_code_normalized(monkeypatch, sleeps):
    """裸 6 位代码归一化为 ts_code:6 开头→.SH,4/8 开头→.BJ,其余→.SZ;已含 . 原样。"""
    pro = FakePro(lambda n, kw: pd.DataFrame({"ts_code": [kw["ts_code"]]}))
    monkeypatch.setattr(tushare_src, "get_pro", lambda *a, **kw: pro)
    CATALOG["tushare.fina_indicator"].fetch(
        {"symbols": ["600519", "000001", "830001", "430047", "000001.SZ"],
         "interval_sec": 0}, CTX)
    assert [kw["ts_code"] for _, kw in pro.calls] == [
        "600519.SH", "000001.SZ", "830001.BJ", "430047.BJ", "000001.SZ"]

    bars = []

    def fake_pro_bar(pro, **kw):
        bars.append(kw["ts_code"])
        return pd.DataFrame({"close": [1.0]})

    monkeypatch.setattr(tushare_src, "pro_bar", fake_pro_bar)
    CATALOG["tushare.pro_bar_daily"].fetch(
        {"symbols": ["600519", "830001"], "interval_sec": 0}, CTX)
    assert bars == ["600519.SH", "830001.BJ"]


def test_tushare_pro_bar_daily_window(monkeypatch, sleeps):
    bars = []

    def fake_pro_bar(pro, **kw):
        bars.append(kw)
        return pd.DataFrame({"trade_date": ["20260611"], "close": [10.0]})

    monkeypatch.setattr(tushare_src, "get_pro", lambda *a, **kw: object())
    monkeypatch.setattr(tushare_src, "pro_bar", fake_pro_bar)
    cols, rows = CATALOG["tushare.pro_bar_daily"].fetch(
        {"symbols": ["000001.SZ"]}, CTX)
    assert bars == [{"ts_code": "000001.SZ", "adj": "qfq",
                     "start_date": "20260513", "end_date": "20260612"}]
    assert rows == [("000001.SZ", "20260611", 10.0)]


# ---------- mootdx ----------

class FakeTdxClient:
    def __init__(self):
        self.quote_batches = []
        self.finance_syms = []
        self.closed = False

    def quotes(self, symbol):
        self.quote_batches.append(list(symbol))
        return pd.DataFrame({"code": list(symbol),
                             "price": [1.0] * len(symbol)})

    def finance(self, symbol):
        self.finance_syms.append(symbol)
        return pd.DataFrame({"code": [symbol], "bps": [np.nan]})

    def close(self):
        self.closed = True


def _fake_mootdx(monkeypatch):
    holder = {}

    class FakeQuotes:
        @staticmethod
        def factory(market):
            assert market == "std"
            holder["client"] = FakeTdxClient()
            return holder["client"]

    qmod = _fake_module("mootdx.quotes", Quotes=FakeQuotes)
    mmod = _fake_module("mootdx", quotes=qmod)
    monkeypatch.setitem(sys.modules, "mootdx", mmod)
    monkeypatch.setitem(sys.modules, "mootdx.quotes", qmod)
    return holder


def test_mootdx_quotes_l5_batches(monkeypatch, sleeps):
    holder = _fake_mootdx(monkeypatch)
    symbols = [f"{i:06d}" for i in range(81)]  # 80+1 → 两批
    cols, rows = CATALOG["mootdx.quotes_l5"].fetch(
        {"symbols": symbols, "interval_sec": 0.03}, CTX)
    cli = holder["client"]
    assert [len(b) for b in cli.quote_batches] == [80, 1]
    assert len(rows) == 81 and cols[:2] == ["code", "price"]
    assert sleeps == [0.03] and cli.closed


def test_mootdx_finance_per_symbol(monkeypatch, sleeps):
    holder = _fake_mootdx(monkeypatch)
    cols, rows = CATALOG["mootdx.finance"].fetch(
        {"symbols": ["000001", "600519.SH"], "interval_sec": 0}, CTX)
    cli = holder["client"]
    assert cli.finance_syms == ["000001", "600519"]  # 去掉交易所后缀
    assert cols == ["symbol", "code", "bps"]
    assert rows == [("000001", "000001", None), ("600519.SH", "600519", None)]
    assert sleeps == [0.0] and cli.closed


# ---------- tencent ----------

def test_tencent_all_codes_falls_back_to_tushare(monkeypatch):
    """东财 push2 代码全集不可达时,经 tushare stock_basic 兜底并保留 sh/sz/bj 前缀。"""
    from backend.services.collectors import tencent, tushare_client

    class FakeBrokenClient:
        def get(self, url, **kw):
            raise RuntimeError("push2 域名被屏蔽")

    class FakeBasicPro:
        def stock_basic(self, list_status, fields):
            assert (list_status, fields) == ("L", "ts_code")
            return pd.DataFrame({"ts_code": ["000001.SZ", "600519.SH", "830799.BJ"]})

    monkeypatch.setattr(tushare_client, "get_pro", lambda *a, **kw: FakeBasicPro())
    assert tencent._all_codes(FakeBrokenClient()) == [
        "sz000001", "sh600519", "bj830799"]
