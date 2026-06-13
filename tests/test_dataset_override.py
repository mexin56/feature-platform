"""Phase 5.2:内置数据集覆盖编辑(override)全量测试。
覆盖模式:POST /custom 对内置 key → is_override=True;
GET /api/datasets 展示 overridden/edit_template/editable;
DELETE override → 恢复默认;data_collect 插件优先走 override。
全部 mock(httpx.request monkeypatch),不触网。
"""
import json
import sqlite3
from datetime import datetime

import duckdb
import pytest

from backend.services.collectors import CATALOG, available
from backend.services.collectors import custom as custom_mod
from backend.services.collectors.base import DataSet
from backend.services.plugins import get_plugin
from backend.services.templating import build_context

CTX = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))
DT = "2026-06-12"

# 测试用内置 key(东方财富概念板块,pure-HTTP,无需 token)
BUILTIN_KEY = "eastmoney.concept_boards"
BUILTIN_SOURCE = "eastmoney"
BUILTIN_DATASET = "concept_boards"

# tushare 内置 key
TUSHARE_KEY = "tushare.daily"
TUSHARE_SOURCE = "tushare"
TUSHARE_DATASET = "daily"


class FakeResp:
    def __init__(self, json_data=None, status=200):
        self._json = json_data
        self.status_code = status

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_dev(client, admin_headers, name="dev1", project="覆盖测试"):
    client.post("/api/users", json={"username": name, "password": name + "123456",
                                    "role": "developer"}, headers=admin_headers)
    h = _login(client, name, name + "123456")
    pid = client.post("/api/projects", json={"name": project, "description": ""},
                      headers=h).json()["id"]
    return {**h, "X-Project-Id": str(pid)}, pid


def _override_body(**overrides):
    """默认:用 http_json 覆盖 eastmoney.concept_boards。"""
    body = {
        "source": BUILTIN_SOURCE,
        "dataset": BUILTIN_DATASET,
        "name": "覆盖概念板块",
        "description": "测试覆盖",
        "mode": "snapshot",
        "collector_type": "http_json",
        "config": {
            "url": "http://fake.test/boards?dt={dt}",
            "records_path": "data",
        },
    }
    body.update(overrides)
    return body


# ---------- Test 1: 创建 override + GET 展示 + 重复 400 ----------

def test_create_override_shows_overridden_in_list(client, admin_headers):
    h, _ = _mk_dev(client, admin_headers)

    # 先确认目标 key 是内置的
    assert BUILTIN_KEY in CATALOG, f"{BUILTIN_KEY} 不在 CATALOG"

    r = client.post("/api/datasets/custom", json=_override_body(), headers=h)
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["key"] == BUILTIN_KEY
    assert row["is_override"] is True
    assert isinstance(row["id"], int)

    # GET /api/datasets — 该 builtin 行应显示 overridden:true
    items = client.get("/api/datasets", headers=h).json()
    by_key = {d["key"]: d for d in items}

    item = by_key[BUILTIN_KEY]
    assert item["editable"] is True
    assert item["overridden"] is True
    assert item["id"] == row["id"]
    assert item["collector_type"] == "http_json"
    assert item["config"]["url"] == "http://fake.test/boards?dt={dt}"
    # overridden 行不含 edit_template
    assert "edit_template" not in item
    # 内置字段保留
    assert item["source"] == BUILTIN_SOURCE
    assert item["target_table"] == CATALOG[BUILTIN_KEY].target_table

    # 第二次 POST 同 key → 400 "已存在覆盖"
    r2 = client.post("/api/datasets/custom", json=_override_body(), headers=h)
    assert r2.status_code == 400, r2.text
    assert "已存在覆盖" in r2.json()["detail"]


# ---------- Test 2: data_collect 插件优先走 override ----------

def test_data_collect_uses_override_not_builtin(client, admin_headers, monkeypatch):
    h, _ = _mk_dev(client, admin_headers, name="dev2", project="插件覆盖测试")

    # 创建 override
    r = client.post("/api/datasets/custom", json=_override_body(), headers=h)
    assert r.status_code == 201, r.text
    ov_id = r.json()["id"]

    # monkeypatch httpx.request → 返回 override 的假数据(2 条记录)
    fake_payload = {"data": [
        {"code": "TEST001", "name": "测试板块A"},
        {"code": "TEST002", "name": "测试板块B"},
    ]}
    http_calls = []

    def fake_request(method, url, **kw):
        http_calls.append(url)
        return FakeResp(json_data=fake_payload)

    monkeypatch.setattr(custom_mod.httpx, "request", fake_request)

    s = client.app.state.settings
    fn = get_plugin("data_collect")
    result = fn({"dataset_key": BUILTIN_KEY}, dict(CTX), s)

    # 应该走了 override 的 http_json(target_table 来自内置)
    assert result["table"] == CATALOG[BUILTIN_KEY].target_table
    assert result["rows"] == 2
    assert result["dt"] == DT
    # 确认 HTTP 请求被触发(走了 override 的 http_json 而不是内置的 httpx)
    assert len(http_calls) == 1
    assert "fake.test" in http_calls[0]

    # 验证写入 duckdb
    con = duckdb.connect(str(s.market_db), read_only=True)
    try:
        tbl = CATALOG[BUILTIN_KEY].target_table
        rows = con.execute(f'select code, name from "{tbl}" order by code').fetchall()
        assert rows == [("TEST001", "测试板块A"), ("TEST002", "测试板块B")]
    finally:
        con.close()

    # DELETE override → 解析回退内置:resolve_custom 不再命中,CATALOG 提供内置 DataSet
    dr = client.delete(f"/api/datasets/custom/{ov_id}", headers=h)
    assert dr.status_code == 200, dr.text
    assert custom_mod.resolve_custom(BUILTIN_KEY, s.db_path) is None
    # 内置 fetch 不经 custom_mod.httpx;monkeypatch CATALOG 内置条目验证回退到内置实现
    builtin_calls = []

    def builtin_fetch(args, ctx):
        builtin_calls.append(args)
        return ["code", "name"], [("BUILTIN001", "内置板块")]

    monkeypatch.setitem(CATALOG, BUILTIN_KEY, DataSet(
        key=BUILTIN_KEY, source=BUILTIN_SOURCE, name="概念板块列表",
        module="eastmoney", desc="", mode="snapshot", requires=None,
        target_table=CATALOG[BUILTIN_KEY].target_table, fetch=builtin_fetch))
    result2 = fn({"dataset_key": BUILTIN_KEY}, dict(CTX), s)
    # 回退到内置:http_calls 不再增加(仍为 1),内置 fetch 被调用
    assert len(http_calls) == 1
    assert len(builtin_calls) == 1
    assert result2["rows"] == 1


# ---------- Test 2b: resolve_custom 优先级(override 命中) ----------

def test_resolve_custom_returns_override_over_catalog(client, admin_headers):
    h, _ = _mk_dev(client, admin_headers, name="dev2b", project="解析优先级测试")
    # 创建 override 前:resolve_custom 未命中,插件解析走 CATALOG
    s = client.app.state.settings
    assert custom_mod.resolve_custom(BUILTIN_KEY, s.db_path) is None
    assert BUILTIN_KEY in CATALOG

    r = client.post("/api/datasets/custom", json=_override_body(), headers=h)
    assert r.status_code == 201, r.text

    # 创建 override 后:resolve_custom 返回 override(http_json),优先于内置
    ds = custom_mod.resolve_custom(BUILTIN_KEY, s.db_path)
    assert ds is not None
    assert ds.key == BUILTIN_KEY
    assert ds.module == "httpx"  # http_json 采集器(内置为 eastmoney 模块)
    # target_table 仍为内置表
    assert ds.target_table == CATALOG[BUILTIN_KEY].target_table


# ---------- Test 2c: viewer 权限(GET 200,写操作 403) ----------

def test_viewer_can_read_but_not_override(client, admin_headers):
    h, pid = _mk_dev(client, admin_headers, name="dev2c", project="覆盖权限测试")
    uid = client.post("/api/users", json={"username": "ro2", "password": "ro2123456",
                                          "role": "viewer"}, headers=admin_headers).json()["id"]
    client.post(f"/api/projects/{pid}/members", json={"user_id": uid}, headers=h)
    ro = {**_login(client, "ro2", "ro2123456"), "X-Project-Id": str(pid)}

    # 开发者先建一个 override 供 viewer 尝试改/删
    ov_id = client.post("/api/datasets/custom", json=_override_body(),
                        headers=h).json()["id"]

    # viewer GET 200 且能看到 editable/edit_template 等字段
    g = client.get("/api/datasets", headers=ro)
    assert g.status_code == 200, g.text
    by_key = {d["key"]: d for d in g.json()}
    assert by_key[BUILTIN_KEY]["editable"] is True
    assert by_key[TUSHARE_KEY]["edit_template"]["collector_type"] == "tushare_api"

    # viewer 写操作全部 403
    assert client.post("/api/datasets/custom", json=_override_body(source="tencent",
                       dataset="spot"), headers=ro).status_code == 403
    assert client.put(f"/api/datasets/custom/{ov_id}", json={"name": "x"},
                      headers=ro).status_code == 403
    assert client.delete(f"/api/datasets/custom/{ov_id}",
                         headers=ro).status_code == 403


# ---------- Test 3: DELETE override → 恢复默认 ----------

def test_delete_override_restores_default(client, admin_headers):
    h, _ = _mk_dev(client, admin_headers, name="dev3", project="恢复默认测试")

    # 创建 override
    r = client.post("/api/datasets/custom", json=_override_body(), headers=h)
    assert r.status_code == 201, r.text
    ov_id = r.json()["id"]

    # 确认 overridden
    items = client.get("/api/datasets", headers=h).json()
    assert {d["key"]: d for d in items}[BUILTIN_KEY]["overridden"] is True

    # 删除 override
    dr = client.delete(f"/api/datasets/custom/{ov_id}", headers=h)
    assert dr.status_code == 200, dr.text

    # GET 应恢复 overridden:false + edit_template 出现
    items2 = client.get("/api/datasets", headers=h).json()
    item = {d["key"]: d for d in items2}[BUILTIN_KEY]
    assert item["overridden"] is False
    assert item["collector_type"] is None
    assert item["config"] is None
    assert "edit_template" in item
    et = item["edit_template"]
    # eastmoney 非 tushare → http_json 模板
    assert et["collector_type"] == "http_json"
    assert et["config"]["url"] == ""
    assert et["mode"] == CATALOG[BUILTIN_KEY].mode


# ---------- Test 4: edit_template 字段(tushare vs 非 tushare) ----------

def test_edit_template_tushare_vs_non_tushare(client, admin_headers):
    h, _ = _mk_dev(client, admin_headers, name="dev4", project="模板测试")

    items = client.get("/api/datasets", headers=h).json()
    by_key = {d["key"]: d for d in items}

    # tushare 内置行 → edit_template.collector_type = tushare_api, api_name = dataset
    tushare_item = by_key.get(TUSHARE_KEY)
    assert tushare_item is not None, f"{TUSHARE_KEY} 不在目录"
    assert tushare_item["editable"] is True
    assert tushare_item.get("overridden") is False
    et = tushare_item["edit_template"]
    assert et["collector_type"] == "tushare_api"
    assert et["config"]["api_name"] == TUSHARE_DATASET  # dataset part of key
    assert et["config"]["params"] == {}
    assert et["config"]["fields"] == ""
    assert et["mode"] == CATALOG[TUSHARE_KEY].mode

    # 非 tushare 内置行 → edit_template.collector_type = http_json
    em_item = by_key.get(BUILTIN_KEY)
    assert em_item is not None
    et2 = em_item["edit_template"]
    assert et2["collector_type"] == "http_json"
    assert et2["config"]["url"] == ""
    assert et2["mode"] == CATALOG[BUILTIN_KEY].mode


# ---------- Test 5: 纯自定义 create/edit 回归测试 ----------

def test_pure_custom_create_and_edit_regression(client, admin_headers):
    h, _ = _mk_dev(client, admin_headers, name="dev5", project="纯自定义回归")

    body = {
        "source": "mytest", "dataset": "prices",
        "name": "测试价格", "description": "纯自定义",
        "mode": "snapshot", "collector_type": "http_json",
        "config": {"url": "http://fake.test/prices", "records_path": "list"},
    }
    r = client.post("/api/datasets/custom", json=body, headers=h)
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["key"] == "mytest.prices"
    assert row["custom"] is True
    assert row["is_override"] is False
    cid = row["id"]

    # 编辑
    pu = client.put(f"/api/datasets/custom/{cid}", headers=h,
                    json={"name": "新价格名", "mode": "per_symbol",
                          "config": {"url": "http://fake.test/{symbol}", "records_path": "l"}})
    assert pu.status_code == 200, pu.text
    assert pu.json()["name"] == "新价格名"
    assert pu.json()["mode"] == "per_symbol"

    # GET — 纯自定义行 editable:true, custom:true, no overridden field
    items = client.get("/api/datasets", headers=h).json()
    item = {d["key"]: d for d in items}["mytest.prices"]
    assert item["editable"] is True
    assert item.get("custom") is True
    assert "overridden" not in item

    # 重复创建 → 400
    r2 = client.post("/api/datasets/custom", json=body, headers=h)
    assert r2.status_code == 400
    assert "已存在" in r2.json()["detail"]


# ---------- Test 6: seed-workflow 含 override key 仍可构建 ----------

def test_seed_workflow_with_overridden_builtin_key(client, admin_headers):
    h, _ = _mk_dev(client, admin_headers, name="dev6", project="seed覆盖测试")

    # 创建 override
    r = client.post("/api/datasets/custom", json=_override_body(), headers=h)
    assert r.status_code == 201, r.text

    # seed-workflow 使用 override key
    seed_body = {
        "name": "覆盖采集流",
        "dataset_keys": [BUILTIN_KEY],
    }
    sr = client.post("/api/datasets/seed-workflow", headers=h, json=seed_body)
    assert sr.status_code == 200, sr.text
    assert sr.json()["task_count"] == 1

    # 验证 DAG
    wf = client.get(f"/api/workflows/{sr.json()['id']}", headers=h).json()
    node_key = BUILTIN_KEY.replace(".", "__")
    by_key = {n["key"]: n for n in wf["dag"]["nodes"]}
    assert node_key in by_key
    assert by_key[node_key]["params"]["dataset_key"] == BUILTIN_KEY


# ---------- Test 7: CustomDataset defaults is_override=False ----------

def test_custom_dataset_default_is_override_false(client, admin_headers):
    h, _ = _mk_dev(client, admin_headers, name="dev7", project="默认值测试")

    body = {
        "source": "deftest", "dataset": "data",
        "name": "默认值测试", "description": "",
        "mode": "snapshot", "collector_type": "http_json",
        "config": {"url": "http://x.test/d", "records_path": ""},
    }
    r = client.post("/api/datasets/custom", json=body, headers=h)
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["is_override"] is False

    # 从 sqlite 直接验证列默认值
    s = client.app.state.settings
    con = sqlite3.connect(str(s.db_path))
    try:
        db_row = con.execute(
            "select is_override from custom_datasets where key = 'deftest.data'"
        ).fetchone()
        assert db_row is not None
        assert db_row[0] == 0  # INTEGER DEFAULT 0
    finally:
        con.close()
