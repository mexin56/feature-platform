"""Phase5.1:自定义数据集(CRUD/http_json+tushare 执行器/插件解析/测试拉取/seed 合并)。
全部 mock(httpx.request 与 get_pro monkeypatch),不触网。"""
import json
import sqlite3
import time
from datetime import datetime

import duckdb
import numpy as np
import pandas as pd
import pytest

from backend.services.collectors import available
from backend.services.collectors import custom
from backend.services.plugins import get_plugin
from backend.services.templating import build_context

CTX = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))
DT = "2026-06-12"  # data_interval_end 的日期 = 采集 dt


class FakeResp:
    def __init__(self, json_data=None, status=200):
        self._json = json_data
        self.status_code = status

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture()
def sleeps(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: calls.append(s))
    return calls


def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_dev_with_project(client, admin_headers, name="bob", project="自定义采集"):
    client.post("/api/users", json={"username": name, "password": name + "123456",
                                    "role": "developer"}, headers=admin_headers)
    h = _login(client, name, name + "123456")
    pid = client.post("/api/projects", json={"name": project, "description": ""},
                      headers=h).json()["id"]
    return {**h, "X-Project-Id": str(pid)}, pid


def _http_body(**over):
    body = {"source": "myapi", "dataset": "spot", "name": "我的快照",
            "description": "测试用", "mode": "snapshot", "collector_type": "http_json",
            "config": {"url": "http://x.test/list?d={dt_nodash}",
                       "records_path": "data.list"}}
    body.update(over)
    return body


# ---------- CRUD ----------

def test_create_custom_dataset_returns_full_row(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    r = client.post("/api/datasets/custom", json=_http_body(), headers=h)
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["key"] == "myapi.spot" and row["custom"] is True
    assert isinstance(row["id"], int)
    assert row["source"] == "myapi" and row["dataset"] == "spot"
    assert row["target_table"] == "ods_myapi_spot"  # 自动派生
    assert row["name"] == "我的快照" and row["description"] == "测试用"
    assert row["mode"] == "snapshot" and row["collector_type"] == "http_json"
    assert row["config"]["records_path"] == "data.list"


def test_catalog_merges_custom_rows_after_builtins(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    rid = client.post("/api/datasets/custom", json=_http_body(), headers=h).json()["id"]
    # POST now returns 201
    items = client.get("/api/datasets", headers=h).json()
    by_key = {d["key"]: d for d in items}
    row = by_key["myapi.spot"]
    assert row["custom"] is True and row["id"] == rid
    assert row["collector_type"] == "http_json"
    assert row["config"]["records_path"] == "data.list"
    assert row["available"] is True and row["reason"] == ""
    assert row["stats"] is None  # market.duckdb 尚无该表
    assert row["target_table"] == "ods_myapi_spot"
    assert row["mode"] == "snapshot" and row["source"] == "myapi"
    assert "custom" not in by_key["tencent.spot"]  # 内置行不带 custom 标记
    assert items.index(by_key["myapi.spot"]) > items.index(by_key["tencent.spot"])


def test_update_custom_dataset_key_immutable(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    rid = client.post("/api/datasets/custom", json=_http_body(), headers=h).json()["id"]
    r = client.put(f"/api/datasets/custom/{rid}", headers=h, json={
        "name": "新名字", "mode": "per_symbol",
        "config": {"url": "http://x.test/q/{symbol}", "records_path": "l"}})
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["name"] == "新名字" and row["mode"] == "per_symbol"
    assert row["config"]["url"] == "http://x.test/q/{symbol}"
    assert row["key"] == "myapi.spot"  # key/target_table 不可变
    assert row["target_table"] == "ods_myapi_spot"
    assert client.put("/api/datasets/custom/99999", json={"name": "x"},
                      headers=h).status_code == 404


def test_delete_custom_dataset(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    rid = client.post("/api/datasets/custom", json=_http_body(), headers=h).json()["id"]
    assert client.delete(f"/api/datasets/custom/{rid}", headers=h).status_code == 200
    keys = {d["key"] for d in client.get("/api/datasets", headers=h).json()}
    assert "myapi.spot" not in keys
    assert client.delete(f"/api/datasets/custom/{rid}", headers=h).status_code == 404


def test_create_builtin_key_creates_override(client, admin_headers):
    """Phase 5.2: 内置 key 创建 → override (201), 二次同 key → 400。"""
    h, _ = _mk_dev_with_project(client, admin_headers)
    # tencent.spot 是内置 key → 创建 override 成功
    r = client.post("/api/datasets/custom", headers=h,
                    json=_http_body(source="tencent", dataset="spot",
                                    config={"url": "http://x.test/tencent", "records_path": ""}))
    assert r.status_code == 201, r.text
    assert r.json()["is_override"] is True
    # 第二次 → 400 已存在覆盖
    r2 = client.post("/api/datasets/custom", headers=h,
                     json=_http_body(source="tencent", dataset="spot",
                                     config={"url": "http://x.test/tencent2", "records_path": ""}))
    assert r2.status_code == 400 and "已存在覆盖" in r2.json()["detail"]


def test_create_rejects_duplicate_custom_key(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    assert client.post("/api/datasets/custom", json=_http_body(), headers=h).status_code == 201
    r = client.post("/api/datasets/custom", json=_http_body(), headers=h)
    assert r.status_code == 400 and "已存在" in r.json()["detail"]


@pytest.mark.parametrize("source,dataset", [
    ("My-API", "spot"),     # 大写/连字符
    ("myapi", "S"),         # 过短
    ("myapi", "a" * 33),    # 过长
    ("", "spot"),           # 空
    ("myapi", "中文"),       # 非 slug 字符
])
def test_create_rejects_bad_slug(client, admin_headers, source, dataset):
    h, _ = _mk_dev_with_project(client, admin_headers)
    r = client.post("/api/datasets/custom", headers=h,
                    json=_http_body(source=source, dataset=dataset))
    assert r.status_code == 400, r.text


def test_create_validates_config_and_enums(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    r = client.post("/api/datasets/custom", json=_http_body(config={}), headers=h)
    assert r.status_code == 400 and "url" in r.json()["detail"]
    r = client.post("/api/datasets/custom", headers=h, json=_http_body(
        collector_type="tushare_api", config={"params": {}}))
    assert r.status_code == 400 and "api_name" in r.json()["detail"]
    assert client.post("/api/datasets/custom", json=_http_body(mode="hourly"),
                       headers=h).status_code == 400
    assert client.post("/api/datasets/custom", json=_http_body(collector_type="grpc"),
                       headers=h).status_code == 400


def test_viewer_cannot_create_custom(client, admin_headers):
    h, pid = _mk_dev_with_project(client, admin_headers)
    uid = client.post("/api/users", json={"username": "ro", "password": "ro123456",
                                          "role": "viewer"}, headers=admin_headers).json()["id"]
    client.post(f"/api/projects/{pid}/members", json={"user_id": uid}, headers=h)
    ro = {**_login(client, "ro", "ro123456"), "X-Project-Id": str(pid)}
    assert client.post("/api/datasets/custom", json=_http_body(), headers=ro).status_code == 403


# ---------- http_json 执行器 ----------

def test_http_json_auto_fields_and_records_path(monkeypatch):
    seen = {}
    payload = {"data": {"list": [
        {"b": 1, "a": "x", "c": {"n": 1}},   # 非标量值 → json.dumps
        {"a": "y", "b": 2.5, "c": None},
        "oops",                               # 非 dict 记录容错跳过
    ]}}

    def fake_request(method, url, **kw):
        seen.update(method=method, url=url, **kw)
        return FakeResp(json_data=payload)

    monkeypatch.setattr(custom.httpx, "request", fake_request)
    cols, rows = custom.exec_http_json(
        {"url": "http://x.test/list", "params": {"d": "{dt}", "nd": "{dt_nodash}"},
         "records_path": "data.list"}, {}, CTX)
    assert seen["method"] == "GET"  # 默认 GET
    assert seen["params"] == {"d": DT, "nd": "20260612"}
    assert "User-Agent" in seen["headers"]
    assert cols == ["a", "b", "c"]  # 自动模式 = 首条记录键排序
    assert rows == [("x", 1, json.dumps({"n": 1}, ensure_ascii=False)), ("y", 2.5, None)]


def test_http_json_missing_records_path_tolerated(monkeypatch):
    monkeypatch.setattr(custom.httpx, "request",
                        lambda m, u, **kw: FakeResp(json_data={"data": {}}))
    cols, rows = custom.exec_http_json(
        {"url": "http://x.test/l", "records_path": "data.list.items"}, {}, CTX)
    assert cols == [] and rows == []


def test_http_json_field_map_render_and_post_body(monkeypatch):
    seen = {}

    def fake_request(method, url, **kw):
        seen.update(method=method, url=url, **kw)
        return FakeResp(json_data={"items": [{"f12": "000001", "f2": 10.5, "junk": 1}]})

    monkeypatch.setattr(custom.httpx, "request", fake_request)
    cols, rows = custom.exec_http_json(
        {"url": "http://x.test/{dt_nodash}/list", "method": "post",
         "body": {"day": "{dt}", "page": 1},
         "records_path": "items", "field_map": {"code": "f12", "price": "f2"}},
        {}, CTX)
    assert seen["url"] == "http://x.test/20260612/list"
    assert seen["method"] == "POST"
    assert seen["json"] == {"day": DT, "page": 1}
    assert cols == ["code", "price"] and rows == [("000001", 10.5)]


def test_http_json_per_symbol_prepends_symbol_and_sleeps(monkeypatch, sleeps):
    calls = []

    def fake_request(method, url, **kw):
        calls.append(url)
        return FakeResp(json_data={"l": [{"v": len(calls)}]})

    monkeypatch.setattr(custom.httpx, "request", fake_request)
    cols, rows = custom.exec_http_json(
        {"url": "http://x.test/q/{symbol}", "records_path": "l"},
        {"symbols": ["000001", "600519"]}, CTX, mode="per_symbol")
    assert calls == ["http://x.test/q/000001", "http://x.test/q/600519"]
    assert cols == ["symbol", "v"]
    assert rows == [("000001", 1), ("600519", 2)]
    assert sleeps == [0.5]  # 默认限频间隔,首个不 sleep


def test_http_json_per_symbol_requires_symbols():
    with pytest.raises(RuntimeError, match="symbols"):
        custom.exec_http_json({"url": "http://x.test/q"}, {}, CTX, mode="per_symbol")


# ---------- tushare 执行器 ----------

def test_exec_tushare_renders_params_and_passes_fields(monkeypatch):
    seen = {}

    class FakePro:
        def my_api(self, **kw):
            seen["call"] = kw
            return pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.5]})

    def fake_get_pro(token=None):
        seen["token"] = token
        return FakePro()

    monkeypatch.setattr(custom, "get_pro", fake_get_pro)
    cols, rows = custom.exec_tushare(
        {"api_name": "my_api", "params": {"trade_date": "{dt_nodash}"},
         "fields": "ts_code,close"},
        {}, {**CTX, "tushare_token": "tok-1"})
    assert seen["token"] == "tok-1"
    assert seen["call"] == {"trade_date": "20260612", "fields": "ts_code,close"}
    assert cols == ["ts_code", "close"] and rows == [("000001.SZ", 10.5)]


def test_exec_tushare_omits_empty_fields_and_nan_to_none(monkeypatch):
    seen = {}

    class FakePro:
        def my_api(self, **kw):
            seen["call"] = kw
            return pd.DataFrame({"close": [np.nan]})

    monkeypatch.setattr(custom, "get_pro", lambda token=None: FakePro())
    cols, rows = custom.exec_tushare({"api_name": "my_api", "params": {}}, {}, CTX)
    assert "fields" not in seen["call"]  # 空 fields 不传
    assert rows == [(None,)]  # NaN → None


def test_exec_tushare_per_symbol_ts_code_and_symbol_col(monkeypatch, sleeps):
    calls = []

    class FakePro:
        def fina(self, **kw):
            calls.append(kw)
            return pd.DataFrame({"roe": [1.0 * len(calls)]})

    monkeypatch.setattr(custom, "get_pro", lambda token=None: FakePro())
    cols, rows = custom.exec_tushare(
        {"api_name": "fina", "params": {"ts_code": "{symbol}"}},
        {"symbols": ["600519", "000001"]}, CTX, mode="per_symbol")
    assert [c["ts_code"] for c in calls] == ["600519.SH", "000001.SZ"]  # _ts_code 归一化
    assert cols == ["symbol", "roe"]
    assert rows == [("600519", 1.0), ("000001", 2.0)]  # symbol 列前置原始代码


# ---------- build_dataset / resolve_custom ----------

def test_build_dataset_availability_semantics():
    ds = custom.build_dataset({"key": "x.y", "source": "x", "name": "n",
                               "mode": "snapshot", "collector_type": "http_json",
                               "config": {"url": "u"}, "target_table": "ods_x_y"})
    assert ds.requires is None and available(ds) == (True, "")
    ds2 = custom.build_dataset({"key": "x.z", "source": "x", "name": "n",
                                "mode": "per_symbol", "collector_type": "tushare_api",
                                "config": {"api_name": "daily"},
                                "target_table": "ods_x_z"})
    assert ds2.requires == "token"  # tushare_api 复用 token 语义(仅需 tushare 包)
    with pytest.raises(ValueError, match="采集器类型"):
        custom.build_dataset({"key": "x.q", "source": "x", "name": "n",
                              "mode": "snapshot", "collector_type": "grpc",
                              "config": {}, "target_table": "ods_x_q"})


def test_resolve_custom_unknown_returns_none(tmp_path):
    assert custom.resolve_custom("no.such", tmp_path / "absent.db") is None
    db = tmp_path / "meta.db"
    sqlite3.connect(str(db)).close()  # 存在但无 custom_datasets 表
    assert custom.resolve_custom("no.such", db) is None


def test_resolve_custom_reads_row_via_sqlite(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    client.post("/api/datasets/custom", json=_http_body(), headers=h)
    s = client.app.state.settings
    ds = custom.resolve_custom("myapi.spot", s.db_path)
    assert ds is not None and ds.key == "myapi.spot"
    assert ds.target_table == "ods_myapi_spot" and ds.mode == "snapshot"
    assert custom.resolve_custom("myapi.nope", s.db_path) is None


# ---------- data_collect 插件端到端 ----------

def test_plugin_collects_custom_dataset_end_to_end(client, admin_headers, monkeypatch):
    h, _ = _mk_dev_with_project(client, admin_headers)
    body = _http_body(source="myapi", dataset="flow", config={
        "url": "http://x.test/flow?d={dt_nodash}", "records_path": "data",
        "field_map": {"code": "c", "val": "v"}})
    assert client.post("/api/datasets/custom", json=body, headers=h).status_code == 201
    monkeypatch.setattr(custom.httpx, "request", lambda m, u, **kw: FakeResp(
        json_data={"data": [{"c": "000001", "v": 1.5}, {"c": "600519", "v": 2.5}]}))
    s = client.app.state.settings
    fn = get_plugin("data_collect")
    result = fn({"dataset_key": "myapi.flow"}, dict(CTX), s)
    assert result == {"table": "ods_myapi_flow", "rows": 2, "dt": DT}
    con = duckdb.connect(str(s.market_db), read_only=True)
    try:
        assert con.execute("select code, val, dt from ods_myapi_flow "
                           "order by code").fetchall() == [
            ("000001", 1.5, DT), ("600519", 2.5, DT)]
    finally:
        con.close()


def test_plugin_unknown_key_still_raises(client):
    fn = get_plugin("data_collect")
    with pytest.raises(RuntimeError, match="数据集不存在"):
        fn({"dataset_key": "nope.custom"}, dict(CTX), client.app.state.settings)


# ---------- 测试拉取端点 ----------

def test_custom_test_endpoint_preview_capped_to_five(client, admin_headers, monkeypatch):
    h, _ = _mk_dev_with_project(client, admin_headers)
    monkeypatch.setattr(custom.httpx, "request", lambda m, u, **kw: FakeResp(
        json_data={"l": [{"v": i} for i in range(8)]}))
    r = client.post("/api/datasets/custom/test", headers=h, json={
        "collector_type": "http_json", "mode": "snapshot",
        "config": {"url": "http://x.test/l", "records_path": "l"}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["columns"] == ["v"] and out["row_count"] == 8
    assert out["rows"] == [[0], [1], [2], [3], [4]]  # 预览截 5


def test_custom_test_endpoint_caps_symbols_to_two(client, admin_headers,
                                                  monkeypatch, sleeps):
    h, _ = _mk_dev_with_project(client, admin_headers)
    calls = []

    def fake_request(method, url, **kw):
        calls.append(url)
        return FakeResp(json_data={"l": [{"v": 1}]})

    monkeypatch.setattr(custom.httpx, "request", fake_request)
    r = client.post("/api/datasets/custom/test", headers=h, json={
        "collector_type": "http_json", "mode": "per_symbol",
        "symbols": ["000001", "600519", "000002", "600000"],
        "config": {"url": "http://x.test/{symbol}", "records_path": "l"}})
    assert r.status_code == 200, r.text
    assert len(calls) == 2  # 截前 2 只
    assert r.json()["columns"] == ["symbol", "v"]


def test_custom_test_endpoint_errors_are_400(client, admin_headers, monkeypatch):
    h, _ = _mk_dev_with_project(client, admin_headers)
    r = client.post("/api/datasets/custom/test", headers=h, json={
        "collector_type": "http_json", "mode": "snapshot", "config": {}})
    assert r.status_code == 400 and "url" in r.json()["detail"]

    def boom(method, url, **kw):
        raise RuntimeError("连接被拒绝")

    monkeypatch.setattr(custom.httpx, "request", boom)
    r = client.post("/api/datasets/custom/test", headers=h, json={
        "collector_type": "http_json", "mode": "snapshot",
        "config": {"url": "http://x.test/l"}})
    assert r.status_code == 400 and "连接被拒绝" in r.json()["detail"]
    # per_symbol 不带 symbols → 可读 400
    r = client.post("/api/datasets/custom/test", headers=h, json={
        "collector_type": "http_json", "mode": "per_symbol",
        "config": {"url": "http://x.test/{symbol}"}})
    assert r.status_code == 400


# ---------- seed-workflow 合并解析 ----------

def test_seed_workflow_accepts_custom_key(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    assert client.post("/api/datasets/custom", json=_http_body(),
                       headers=h).status_code == 201
    r = client.post("/api/datasets/seed-workflow", headers=h, json={
        "name": "含自定义采集流", "dataset_keys": ["tencent.spot", "myapi.spot"]})
    assert r.status_code == 200, r.text
    assert r.json()["task_count"] == 2
    wf = client.get(f"/api/workflows/{r.json()['id']}", headers=h).json()
    by_key = {n["key"]: n for n in wf["dag"]["nodes"]}
    assert by_key["myapi__spot"]["params"] == {"dataset_key": "myapi.spot", "args": {}}
    assert wf["dag"]["edges"] == [["tencent__spot", "myapi__spot"]]


def test_seed_workflow_custom_per_symbol_requires_symbols(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    body = _http_body(dataset="hist", mode="per_symbol",
                      config={"url": "http://x.test/{symbol}", "records_path": "l"})
    assert client.post("/api/datasets/custom", json=body, headers=h).status_code == 201
    r = client.post("/api/datasets/seed-workflow", headers=h, json={
        "name": "缺池", "dataset_keys": ["myapi.hist"]})
    assert r.status_code == 400 and "myapi.hist" in r.json()["detail"]
    r = client.post("/api/datasets/seed-workflow", headers=h, json={
        "name": "带池", "dataset_keys": ["myapi.hist"], "symbols": ["000001"]})
    assert r.status_code == 200, r.text


def test_seed_workflow_unknown_key_still_rejected(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    r = client.post("/api/datasets/seed-workflow", headers=h, json={
        "name": "未知", "dataset_keys": ["no.such"]})
    assert r.status_code == 400 and "no.such" in r.json()["detail"]
