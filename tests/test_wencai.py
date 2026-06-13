"""爱问财(同花顺问财/wencai)数据源 + 自定义采集器测试。
全部 mock(pywencai.get 经 sys.modules 注入假模块),不触网、不依赖 node.js。"""
import importlib.machinery
import importlib.util
import sys
import types
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from backend.services.collectors import CATALOG, available
from backend.services.collectors import custom, wencai
from backend.services.collectors.writer import COL_RE
from backend.services.templating import build_context

CTX = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))
DT = "2026-06-12"  # data_interval_end 的日期 = 采集 dt


def _fake_pywencai(monkeypatch, get_fn):
    """注入假 pywencai 模块(仅暴露 get);_run_query 内 import pywencai 命中此模块。
    设置 __spec__ 使 available() 的 importlib.util.find_spec 视该包为已安装。"""
    mod = types.ModuleType("pywencai")
    mod.get = get_fn
    mod.__spec__ = importlib.machinery.ModuleSpec("pywencai", loader=None)
    monkeypatch.setitem(sys.modules, "pywencai", mod)


# ---------- _sanitize_cols ----------

def test_sanitize_strips_bracket_date_suffix():
    assert wencai._sanitize_cols(["涨停[20260612]"]) == ["涨停"]
    assert wencai._sanitize_cols(["首次涨停时间[20260612]"]) == ["首次涨停时间"]
    # 仅剥离尾部方括号段,中间内容保留
    assert wencai._sanitize_cols(["股票代码"]) == ["股票代码"]


def test_sanitize_replaces_punct_and_spaces():
    cols = wencai._sanitize_cols(["a b", "净流入(万)", "涨跌幅-3日", "  "])
    assert cols == ["a_b", "净流入_万", "涨跌幅_3日", "col3"]
    for c in cols:
        assert COL_RE.match(c), c


def test_sanitize_collision_dedupe():
    # 两列剥离后同名 → 加 _2 _3 去重
    cols = wencai._sanitize_cols(["涨停[20260611]", "涨停[20260612]", "涨停"])
    assert cols == ["涨停", "涨停_2", "涨停_3"]
    assert len(set(cols)) == 3


def test_sanitize_all_results_match_col_re():
    raw = ["涨停[20260612]", "a b", "净流入(万)", "涨/跌", "@@@", "code", "code"]
    for c in wencai._sanitize_cols(raw):
        assert COL_RE.match(c), c


# ---------- _run_query ----------

def test_run_query_sanitizes_cols_and_nan_to_none(monkeypatch):
    df = pd.DataFrame({"股票代码": ["000001", "600519"],
                       "涨停[20260612]": ["是", np.nan],
                       "首次涨停时间[20260612]": ["09:30", "10:00"]})
    _fake_pywencai(monkeypatch, lambda query, loop=False: df)
    cols, rows = wencai._run_query("今日涨停", CTX)
    assert cols == ["股票代码", "涨停", "首次涨停时间"]
    assert rows == [("000001", "是", "09:30"), ("600519", None, "10:00")]
    for c in cols:
        assert COL_RE.match(c), c


def test_run_query_renders_dt_placeholders(monkeypatch):
    seen = {}

    def fake_get(query, loop=False):
        seen["query"] = query
        seen["loop"] = loop
        return pd.DataFrame({"x": [1]})

    _fake_pywencai(monkeypatch, fake_get)
    wencai._run_query("{dt} 涨停 日期{dt_nodash}", CTX, loop=True)
    assert seen["query"] == f"{DT} 涨停 日期20260612"
    assert seen["loop"] is True


def test_run_query_none_raises(monkeypatch):
    _fake_pywencai(monkeypatch, lambda query, loop=False: None)
    with pytest.raises(RuntimeError, match="无结果或返回非表格"):
        wencai._run_query("乱七八糟", CTX)


def test_run_query_non_dataframe_dict_raises(monkeypatch):
    # 部分 query_type 返回 dict 而非 DataFrame → 可读 RuntimeError
    _fake_pywencai(monkeypatch, lambda query, loop=False: {"foo": "bar"})
    with pytest.raises(RuntimeError, match="无结果或返回非表格"):
        wencai._run_query("某指标", CTX)


# ---------- 自定义采集器 wencai ----------

def test_build_dataset_wencai_requires_package_pywencai():
    ds = custom.build_dataset({"key": "x.w", "source": "x", "name": "n",
                               "mode": "snapshot", "collector_type": "wencai",
                               "config": {"query": "今日涨停"},
                               "target_table": "ods_x_w"})
    assert ds.requires == "package" and ds.module == "pywencai"
    # available() 经 ds.module 检测 pywencai 包
    has_pkg = importlib.util.find_spec("pywencai") is not None
    assert available(ds)[0] is has_pkg


def test_exec_wencai_renders_query_and_calls_run_query(monkeypatch):
    seen = {}

    def fake_get(query, loop=False):
        seen["query"], seen["loop"] = query, loop
        return pd.DataFrame({"代码[20260612]": ["000001"]})

    _fake_pywencai(monkeypatch, fake_get)
    cols, rows = custom.exec_wencai(
        {"query": "{dt} 涨停", "loop": True}, {}, CTX)
    assert seen["query"] == f"{DT} 涨停" and seen["loop"] is True
    assert cols == ["代码"] and rows == [("000001",)]


def test_exec_wencai_default_loop_false(monkeypatch):
    seen = {}

    def fake_get(query, loop=False):
        seen["loop"] = loop
        return pd.DataFrame({"a": [1]})

    _fake_pywencai(monkeypatch, fake_get)
    custom.exec_wencai({"query": "涨停"}, {}, CTX)
    assert seen["loop"] is False


# ---------- 目录条目 ----------

def test_catalog_includes_wencai_zt_pool():
    ds = CATALOG["wencai.zt_pool"]
    assert ds.source == "wencai" and ds.module == "pywencai"
    assert ds.mode == "snapshot" and ds.requires == "package"
    assert ds.target_table == "ods_wencai_zt_pool"
    has_pkg = importlib.util.find_spec("pywencai") is not None
    assert available(ds)[0] is has_pkg


def test_wencai_zt_pool_fetch_uses_run_query(monkeypatch):
    seen = {}

    def fake_get(query, loop=False):
        seen["query"], seen["loop"] = query, loop
        return pd.DataFrame({"代码": ["000001"]})

    _fake_pywencai(monkeypatch, fake_get)
    cols, rows = CATALOG["wencai.zt_pool"].fetch({}, CTX)
    assert seen["query"] == "今日涨停 非ST" and seen["loop"] is False
    assert cols == ["代码"] and rows == [("000001",)]


# ---------- 创建 / 测试拉取(API 层) ----------

def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username,
                                             "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_dev_with_project(client, admin_headers, name="wc", project="问财采集"):
    client.post("/api/users", json={"username": name, "password": name + "123456",
                                    "role": "developer"}, headers=admin_headers)
    h = _login(client, name, name + "123456")
    pid = client.post("/api/projects", json={"name": project, "description": ""},
                      headers=h).json()["id"]
    return {**h, "X-Project-Id": str(pid)}


def test_create_custom_wencai_missing_query_400(client, admin_headers):
    h = _mk_dev_with_project(client, admin_headers)
    r = client.post("/api/datasets/custom", headers=h, json={
        "source": "mywc", "dataset": "zt", "name": "我的问财",
        "mode": "snapshot", "collector_type": "wencai", "config": {}})
    assert r.status_code == 400 and "query" in r.json()["detail"]


def test_create_custom_wencai_with_query_200(client, admin_headers):
    h = _mk_dev_with_project(client, admin_headers)
    r = client.post("/api/datasets/custom", headers=h, json={
        "source": "mywc", "dataset": "zt", "name": "我的问财",
        "mode": "snapshot", "collector_type": "wencai",
        "config": {"query": "今日涨停 连续涨停天数大于2", "loop": False}})
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["key"] == "mywc.zt" and row["collector_type"] == "wencai"
    assert row["config"]["query"] == "今日涨停 连续涨停天数大于2"
    assert row["target_table"] == "ods_mywc_zt"


def test_custom_test_endpoint_wencai_preview(client, admin_headers, monkeypatch):
    h = _mk_dev_with_project(client, admin_headers)
    _fake_pywencai(monkeypatch, lambda query, loop=False: pd.DataFrame(
        {"代码[20260612]": ["000001", "600519"], "涨停[20260612]": ["是", "是"]}))
    r = client.post("/api/datasets/custom/test", headers=h, json={
        "collector_type": "wencai", "mode": "snapshot",
        "config": {"query": "{dt} 今日涨停"}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["columns"] == ["代码", "涨停"]
    assert out["row_count"] == 2
    assert out["rows"][0] == ["000001", "是"]


def test_custom_test_endpoint_wencai_missing_query_400(client, admin_headers):
    h = _mk_dev_with_project(client, admin_headers)
    r = client.post("/api/datasets/custom/test", headers=h, json={
        "collector_type": "wencai", "mode": "snapshot", "config": {}})
    assert r.status_code == 400 and "query" in r.json()["detail"]


# ---------- akshare 北向接口名修复 ----------

def test_akshare_hsgt_fund_summary_uses_correct_api(monkeypatch):
    # 目录已用正确接口名 stock_hsgt_fund_flow_summary_em(旧名不存在)
    assert "akshare.stock_hsgt_fund_summary" in CATALOG
    assert "akshare.stock_hsgt_north_net_flow_in_em" not in CATALOG
    seen = {}

    def summary():
        seen["called"] = True
        return pd.DataFrame({"板块": ["沪股通"], "资金净流入": [12.3]})

    mod = types.ModuleType("akshare")
    mod.stock_hsgt_fund_flow_summary_em = summary
    monkeypatch.setitem(sys.modules, "akshare", mod)
    cols, rows = CATALOG["akshare.stock_hsgt_fund_summary"].fetch({}, CTX)
    assert seen.get("called") is True
    assert cols == ["板块", "资金净流入"] and rows == [("沪股通", 12.3)]


# ---------- mootdx 友好报错 ----------

def _fake_mootdx_client(monkeypatch, quotes_ret=None, finance_ret=None,
                        quotes_exc=None, finance_exc=None):
    from backend.services.collectors import mootdx_src

    class FakeCli:
        def quotes(self, symbol):
            if quotes_exc is not None:
                raise quotes_exc
            return quotes_ret

        def finance(self, symbol):
            if finance_exc is not None:
                raise finance_exc
            return finance_ret

        def close(self):
            pass

    monkeypatch.setattr(mootdx_src, "_client", lambda: FakeCli())


def test_mootdx_quotes_empty_raises_readable(monkeypatch):
    # 客户端返回 None(未配行情服务器)→ 现有"未返回任何数据"可读错误
    _fake_mootdx_client(monkeypatch, quotes_ret=None)
    with pytest.raises(RuntimeError, match="未返回任何数据"):
        CATALOG["mootdx.quotes_l5"].fetch({"symbols": ["000001"]}, CTX)


def test_mootdx_quotes_cryptic_exc_becomes_readable(monkeypatch):
    _fake_mootdx_client(monkeypatch, quotes_exc=ValueError(
        "not enough values to unpack (expected 2, got 0)"))
    with pytest.raises(RuntimeError, match="bestip"):
        CATALOG["mootdx.quotes_l5"].fetch({"symbols": ["000001"]}, CTX)


def test_mootdx_finance_empty_raises_readable(monkeypatch):
    # finance 返回 None → 可读 bestip 指引(而非静默跳过)
    _fake_mootdx_client(monkeypatch, finance_ret=None)
    with pytest.raises(RuntimeError, match="bestip"):
        CATALOG["mootdx.finance"].fetch({"symbols": ["000001"]}, CTX)


def test_mootdx_finance_cryptic_exc_becomes_readable(monkeypatch):
    _fake_mootdx_client(monkeypatch, finance_exc=ValueError(
        "not enough values to unpack (expected 2, got 0)"))
    with pytest.raises(RuntimeError, match="bestip"):
        CATALOG["mootdx.finance"].fetch({"symbols": ["000001"]}, CTX)
