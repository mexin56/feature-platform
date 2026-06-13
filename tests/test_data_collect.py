"""Phase5 T1:采集框架(writer 幂等/插件/目录可用性/查询 ATTACH/腾讯解析)。无网络。"""
from datetime import datetime

import duckdb
import pytest

from backend.config import Settings
from backend.services.collectors import CATALOG, available
from backend.services.collectors.base import DataSet
from backend.services.collectors.writer import write_market
from backend.services.plugins import get_plugin
from backend.services.templating import build_context

CTX = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))


def _env(tmp_path):
    s = Settings(storage_dir=str(tmp_path))
    s.ensure_dirs()
    return s


def _fake_ds(key="fake.snap", fetch=None, requires=None):
    return DataSet(key=key, source="fake", name="假数据集", module="fake",
                   desc="", mode="snapshot", requires=requires,
                   target_table="ods_fake_snap", fetch=fetch)


# ---------- writer ----------

def test_writer_idempotent_same_dt(tmp_path):
    s = _env(tmp_path)
    cols, rows = ["code", "v"], [("000001", 1.5), ("600519", 2.5)]
    assert write_market(s, "ods_test_x", "2026-06-11", cols, rows,
                        collected_at="2026-06-11T17:00:00") == 2
    # 同 dt 重写:行数不变(先删后插)
    assert write_market(s, "ods_test_x", "2026-06-11", cols, rows,
                        collected_at="2026-06-11T18:00:00") == 2
    con = duckdb.connect(str(s.market_db), read_only=True)
    try:
        assert con.execute("select count(*) from ods_test_x").fetchone()[0] == 2
        # collected_at 以最后一次写入为准
        assert con.execute("select distinct collected_at from ods_test_x").fetchall() == [
            ("2026-06-11T18:00:00",)]
    finally:
        con.close()


def test_writer_accumulates_across_dt(tmp_path):
    s = _env(tmp_path)
    cols, rows = ["code", "v"], [("000001", 1.5)]
    write_market(s, "ods_test_x", "2026-06-11", cols, rows)
    write_market(s, "ods_test_x", "2026-06-12", cols, rows)
    con = duckdb.connect(str(s.market_db), read_only=True)
    try:
        assert con.execute("select count(*) from ods_test_x").fetchone()[0] == 2
        assert con.execute(
            "select count(distinct dt) from ods_test_x").fetchone()[0] == 2
        names = [r[0] for r in con.execute("describe ods_test_x").fetchall()]
        assert names == ["code", "v", "dt", "collected_at"]
    finally:
        con.close()


def test_writer_rejects_bad_table_name(tmp_path):
    s = _env(tmp_path)
    with pytest.raises(ValueError, match="表名"):
        write_market(s, "Bad-Name;drop", "2026-06-11", ["a"], [(1,)])


def test_writer_concurrent_write_friendly_error(tmp_path, monkeypatch):
    """写锁被其他任务占用(duckdb.IOException)→ 友好 RuntimeError,可被节点重试。"""
    s = _env(tmp_path)

    def locked(*a, **kw):
        raise duckdb.IOException("Could not set lock on file")

    monkeypatch.setattr(duckdb, "connect", locked)
    with pytest.raises(RuntimeError, match="正被其他任务写入"):
        write_market(s, "ods_test_x", "2026-06-11", ["a"], [(1,)])


# ---------- 目录与可用性 ----------

def test_catalog_has_tencent_datasets():
    assert "tencent.spot" in CATALOG
    assert "tencent.index_spot" in CATALOG
    ds = CATALOG["tencent.spot"]
    assert ds.target_table == "ods_tencent_spot"
    assert ds.mode == "snapshot"
    ok, reason = available(ds)
    assert ok, reason


def test_available_terminal_and_missing_package():
    ok, reason = available(_fake_ds(requires="terminal", fetch=lambda a, c: ([], [])))
    assert not ok and "终端" in reason
    ds = _fake_ds(requires="package", fetch=lambda a, c: ([], []))
    ds.module = "no_such_pkg_xyz_123"
    ok, reason = available(ds)
    assert not ok and "no_such_pkg_xyz_123" in reason
    ok, reason = available(_fake_ds(fetch=None))  # fetch 缺失 = 不可用
    assert not ok


# ---------- data_collect 插件 ----------

def test_plugin_collects_into_market_db(tmp_path, monkeypatch):
    def fake_fetch(args, ctx):
        assert ctx["ds"] == "2026-06-11"
        return ["code", "price"], [("000001", 10.5), ("600519", 1500.0)]

    monkeypatch.setitem(CATALOG, "fake.snap", _fake_ds(fetch=fake_fetch))
    s = _env(tmp_path)
    fn = get_plugin("data_collect")
    result = fn({"dataset_key": "fake.snap"}, CTX, s)
    assert result == {"table": "ods_fake_snap", "rows": 2, "dt": "2026-06-12"}
    con = duckdb.connect(str(s.market_db), read_only=True)
    try:
        cols = [r[0] for r in con.execute("describe ods_fake_snap").fetchall()]
        assert "dt" in cols and "collected_at" in cols
        assert con.execute("select count(*) from ods_fake_snap "
                           "where dt='2026-06-12'").fetchone()[0] == 2
    finally:
        con.close()


def test_plugin_unknown_dataset_raises(tmp_path):
    fn = get_plugin("data_collect")
    with pytest.raises(RuntimeError, match="数据集不存在"):
        fn({"dataset_key": "nope.nope"}, CTX, _env(tmp_path))


def test_plugin_unavailable_dataset_raises(tmp_path, monkeypatch):
    monkeypatch.setitem(CATALOG, "qmt.tick",
                        _fake_ds(key="qmt.tick", requires="terminal", fetch=None))
    fn = get_plugin("data_collect")
    with pytest.raises(RuntimeError, match="终端"):
        fn({"dataset_key": "qmt.tick"}, CTX, _env(tmp_path))


# ---------- ATTACH 打通(duckdb_sql 插件 + query API) ----------

def test_duckdb_sql_plugin_reads_market(tmp_path):
    s = _env(tmp_path)
    write_market(s, "ods_test_x", "2026-06-11", ["code", "v"], [("a", 1), ("b", 2)])
    fn = get_plugin("duckdb_sql")
    result = fn({"sql": "select * from market.ods_test_x"}, CTX, s)
    assert result["rows"] == 2


def _mk_ws(client, admin_headers):
    r = client.post("/api/auth/login",
                    json={"username": "admin", "password": "admin123"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    pid = client.post("/api/projects", json={"name": "p1", "description": ""},
                      headers=h).json()["id"]
    return {**h, "X-Project-Id": str(pid)}


def test_query_api_reads_market_and_catalog(client, admin_headers):
    h = _mk_ws(client, admin_headers)
    s = client.app.state.settings
    write_market(s, "ods_test_x", "2026-06-11", ["code", "v"], [("a", 1)])
    r = client.post("/api/query", json={
        "engine": "duckdb",
        "sql": "select code, v, dt from market.ods_test_x"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [["a", 1, "2026-06-11"]]
    r = client.get("/api/query/catalog?engine=duckdb", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "views" in body  # 原有键不变
    tbl = next(t for t in body["market_tables"] if t["name"] == "market.ods_test_x")
    assert {"code", "v", "dt", "collected_at"} <= {c["name"] for c in tbl["columns"]}


def test_query_api_without_market_db_still_works(client, admin_headers):
    h = _mk_ws(client, admin_headers)
    r = client.post("/api/query", json={"engine": "duckdb", "sql": "select 1 as a"},
                    headers=h)
    assert r.status_code == 200, r.text
    r = client.get("/api/query/catalog?engine=duckdb", headers=h)
    assert r.json()["market_tables"] == []


# ---------- 腾讯解析 ----------

def _spot_entry(code, name, price, pre, open_, vol, amt, high, low, pct,
                turn, pe, pb, total, float_, ampl):
    p = [""] * 50
    p[0], p[1], p[2] = "51", name, code
    p[3], p[4], p[5] = price, pre, open_
    p[32], p[33], p[34] = pct, high, low
    p[36], p[37], p[38], p[39] = vol, amt, turn, pe
    p[43], p[44], p[45], p[46] = ampl, float_, total, pb
    return f'v_sz{code}="' + "~".join(p) + '";'


def test_parse_spot_columns_and_tolerance():
    from backend.services.collectors import tencent

    text = (_spot_entry("000001", "平安银行", "10.50", "10.40", "10.45", "862190",
                        "90654", "10.60", "10.30", "0.96", "0.44", "4.5",
                        "0.58", "2037", "2036", "2.88")
            + "\n" + 'v_sz000002="51~畸形条目";' + "\n"
            + _spot_entry("600519", "贵州茅台", "1500.0", "1490.0", "1495.0", "20000",
                          "300000", "1510.0", "1480.0", "0.67", "0.16", "22.1",
                          "8.2", "18800", "18800", "2.01"))
    rows = tencent.parse_spot(text)
    assert len(rows) == 2  # 畸形条目被跳过
    assert tencent.SPOT_COLUMNS == [
        "code", "name", "price", "pre_close", "open", "volume_hand", "amount_wan",
        "high", "low", "pct_chg", "turnover_pct", "pe_ttm", "pb",
        "total_mcap_yi", "float_mcap_yi", "amplitude_pct"]
    assert rows[0] == ("000001", "平安银行", 10.50, 10.40, 10.45, 862190.0,
                       90654.0, 10.60, 10.30, 0.96, 0.44, 4.5, 0.58,
                       2037.0, 2036.0, 2.88)


def test_parse_index():
    from backend.services.collectors import tencent

    p = [""] * 40
    p[0], p[1], p[2], p[3] = "1", "上证指数", "000001", "3404.66"
    p[32], p[36], p[37] = "0.07", "36038112", "48519968"
    text = 'v_sh000001="' + "~".join(p) + '";'
    rows = tencent.parse_index(text)
    assert tencent.INDEX_COLUMNS == ["code", "name", "price", "change_pct",
                                     "volume", "amount"]
    assert rows == [("000001", "上证指数", 3404.66, 0.07, 36038112.0, 48519968.0)]
