"""Phase5 T3:数据集目录 API(目录/可用性/market 统计)+ 一键采集工作流 + tushare token 链路。"""
from datetime import datetime

from backend.services.collectors import CATALOG
from backend.services.collectors.base import DataSet
from backend.services.collectors.writer import write_market


def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_dev_with_project(client, admin_headers, name="bob", project="行情采集"):
    client.post("/api/users", json={"username": name, "password": name + "123456",
                                    "role": "developer"}, headers=admin_headers)
    h = _login(client, name, name + "123456")
    pid = client.post("/api/projects", json={"name": project, "description": ""},
                      headers=h).json()["id"]
    return {**h, "X-Project-Id": str(pid)}, pid


SEED = {"name": "每日行情采集", "dataset_keys": ["tencent.spot", "tencent.index_spot"]}


# ---------- 目录 ----------

def test_catalog_lists_entries_with_availability(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    r = client.get("/api/datasets", headers=h)
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) >= 70
    assert {"key", "source", "name", "module", "desc", "mode", "requires",
            "target_table", "available", "reason", "stats"} <= set(items[0])
    by_key = {d["key"]: d for d in items}
    spot = by_key["tencent.spot"]
    assert spot["available"] is True and spot["reason"] == ""
    assert spot["source"] == "tencent" and spot["mode"] == "snapshot"
    assert spot["target_table"] == "ods_tencent_spot"
    assert spot["stats"] is None  # market.duckdb 尚不存在
    qmt = by_key["qmt.full_tick"]
    assert qmt["available"] is False and "QMT" in qmt["reason"]


def test_catalog_stats_reflect_market_db(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    write_market(client.app.state.settings, "ods_tencent_spot", "2026-06-11",
                 ["code", "price"], [("000001", 10.5), ("600519", 1500.0)])
    by_key = {d["key"]: d for d in client.get("/api/datasets", headers=h).json()}
    assert by_key["tencent.spot"]["stats"] == {"rows": 2, "max_dt": "2026-06-11"}
    assert by_key["tencent.index_spot"]["stats"] is None  # 表不存在 → null


def test_catalog_degrades_on_corrupted_market_db(client, admin_headers):
    """写入期间/损坏的 market.duckdb:目录 200 且 stats 全 null,不拖垮接口。"""
    h, _ = _mk_dev_with_project(client, admin_headers)
    p = client.app.state.settings.market_db
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00not a duckdb file\xff" * 64)
    r = client.get("/api/datasets", headers=h)
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) >= 70
    assert all(d["stats"] is None for d in items)


# ---------- seed-workflow ----------

def test_seed_workflow_linear_chain_and_params(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    body = {"name": "每日行情采集", "interval_sec": 1.5, "symbols": ["000001", "600519"],
            "dataset_keys": ["tencent.spot", "sina.financial_report_lrb",
                             "tencent.index_spot"]}
    r = client.post("/api/datasets/seed-workflow", json=body, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["version_no"] == 1 and out["task_count"] == 3
    wf = client.get(f"/api/workflows/{out['id']}", headers=h).json()
    assert wf["name"] == "每日行情采集"
    assert wf["cron"] == "0 17 * * 1-5"  # 默认 cron
    assert wf["timezone"] == "Asia/Shanghai" and wf["catchup"] is False
    assert wf["concurrency_limit"] == 1 and wf["failure_policy"] == "continue"
    assert wf["alert_on_failure"] is True
    keys = [n["key"] for n in wf["dag"]["nodes"]]
    assert sorted(keys) == sorted(["tencent__spot", "sina__financial_report_lrb",
                                   "tencent__index_spot"])
    # 线性串链(按给定顺序)防限频
    assert wf["dag"]["edges"] == [["tencent__spot", "sina__financial_report_lrb"],
                                  ["sina__financial_report_lrb", "tencent__index_spot"]]
    by_key = {n["key"]: n for n in wf["dag"]["nodes"]}
    snap = by_key["tencent__spot"]
    assert snap["type"] == "data_collect"
    assert snap["params"] == {"dataset_key": "tencent.spot", "args": {}}
    assert (snap["retries"], snap["retry_delay_sec"], snap["timeout_sec"]) == (1, 60, 1800)
    per = by_key["sina__financial_report_lrb"]
    assert per["params"] == {"dataset_key": "sina.financial_report_lrb",
                             "args": {"symbols": ["000001", "600519"],
                                      "interval_sec": 1.5}}
    # per_symbol 动态超时:max(1800, 股票数*间隔*2+600);小池取下限 1800
    assert per["timeout_sec"] == max(1800, int(2 * 1.5 * 2 + 600)) == 1800


def test_seed_workflow_per_symbol_dynamic_timeout(client, admin_headers):
    """大股票池逐股节点超时随池子放大,snapshot 节点维持固定 1800。"""
    h, _ = _mk_dev_with_project(client, admin_headers)
    symbols = [f"{i:06d}" for i in range(2000)]
    body = {"name": "大池采集", "interval_sec": 0.5, "symbols": symbols,
            "dataset_keys": ["sina.financial_report_lrb", "tencent.spot"]}
    r = client.post("/api/datasets/seed-workflow", json=body, headers=h)
    assert r.status_code == 200, r.text
    wf = client.get(f"/api/workflows/{r.json()['id']}", headers=h).json()
    by_key = {n["key"]: n for n in wf["dag"]["nodes"]}
    # max(1800, 2000*0.5*2+600) = 2600
    assert by_key["sina__financial_report_lrb"]["timeout_sec"] == 2600
    assert by_key["tencent__spot"]["timeout_sec"] == 1800  # snapshot 固定


def test_seed_workflow_per_symbol_requires_symbols(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    r = client.post("/api/datasets/seed-workflow", headers=h, json={
        "name": "缺股票池", "dataset_keys": ["tencent.spot", "sina.financial_report_lrb"]})
    assert r.status_code == 400
    assert "sina.financial_report_lrb" in r.json()["detail"]


def test_seed_workflow_unknown_key_rejected(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    r = client.post("/api/datasets/seed-workflow", headers=h, json={
        "name": "未知集", "dataset_keys": ["tencent.spot", "no.such"]})
    assert r.status_code == 400 and "no.such" in r.json()["detail"]


def test_seed_workflow_unavailable_key_rejected(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    r = client.post("/api/datasets/seed-workflow", headers=h, json={
        "name": "不可用集", "dataset_keys": ["qmt.full_tick"]})
    assert r.status_code == 400 and "qmt.full_tick" in r.json()["detail"]


def test_seed_workflow_empty_keys_rejected(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    r = client.post("/api/datasets/seed-workflow", headers=h,
                    json={"name": "空集", "dataset_keys": []})
    assert r.status_code in (400, 422)


def test_seed_workflow_duplicate_name_bubbles_400(client, admin_headers):
    h, _ = _mk_dev_with_project(client, admin_headers)
    assert client.post("/api/datasets/seed-workflow", json=SEED, headers=h).status_code == 200
    assert client.post("/api/datasets/seed-workflow", json=SEED, headers=h).status_code == 400


def test_viewer_can_read_catalog_but_not_seed(client, admin_headers):
    h, pid = _mk_dev_with_project(client, admin_headers)
    uid = client.post("/api/users", json={"username": "ro", "password": "ro123456",
                                          "role": "viewer"}, headers=admin_headers).json()["id"]
    client.post(f"/api/projects/{pid}/members", json={"user_id": uid}, headers=h)
    ro = {**_login(client, "ro", "ro123456"), "X-Project-Id": str(pid)}
    assert client.get("/api/datasets", headers=ro).status_code == 200
    assert client.post("/api/datasets/seed-workflow", json=SEED, headers=ro).status_code == 403


# ---------- tushare token 链路 ----------

def test_tushare_token_setting_roundtrip(client, admin_headers):
    r = client.put("/api/settings/tushare_token", json={"value": "tok-xyz"},
                   headers=admin_headers)
    assert r.status_code == 200, r.text
    r = client.get("/api/settings/tushare_token", headers=admin_headers)
    assert r.json() == {"key": "tushare_token", "value": "tok-xyz"}


def test_ctx_token_reaches_get_pro(monkeypatch):
    import pandas as pd

    from backend.services.collectors import tushare_src

    seen = {}

    class FakePro:
        def daily(self, trade_date):
            seen["trade_date"] = trade_date
            return pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.5]})

    def fake_get_pro(token=None):
        seen["token"] = token
        return FakePro()

    monkeypatch.setattr(tushare_src, "get_pro", fake_get_pro)
    ctx = {"data_interval_end": "2026-06-11T17:00:00"}
    CATALOG["tushare.daily"].fetch({}, {**ctx, "tushare_token": "tok-abc"})
    assert seen["token"] == "tok-abc" and seen["trade_date"] == "20260611"
    CATALOG["tushare.daily"].fetch({}, ctx)  # 无 ctx token → 走内置默认(None 透传)
    assert seen["token"] is None


def test_plugin_injects_token_from_system_settings(client, admin_headers, monkeypatch):
    from backend.services.plugins import get_plugin
    from backend.services.templating import build_context

    seen = {}

    def fake_fetch(args, ctx):
        seen["token"] = ctx.get("tushare_token")
        return ["a"], [(1,)]

    monkeypatch.setitem(CATALOG, "fake.tok", DataSet(
        key="fake.tok", source="fake", name="假token集", module="tushare", desc="",
        mode="snapshot", requires="token", target_table="ods_fake_tok",
        fetch=fake_fetch))
    fn = get_plugin("data_collect")
    s = client.app.state.settings
    ctx = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))
    fn({"dataset_key": "fake.tok"}, ctx, s)
    assert seen["token"] is None  # 未配置 → 不注入,fetch 走默认 token
    client.put("/api/settings/tushare_token", json={"value": "tok-from-db"},
               headers=admin_headers)
    ctx = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))
    fn({"dataset_key": "fake.tok"}, ctx, s)
    assert seen["token"] == "tok-from-db"  # SystemSetting 经插件注入 ctx
