import json
from datetime import datetime

import duckdb
import pytest
from sqlalchemy.orm import sessionmaker

from backend.config import Settings
from backend.db import Base, make_engine
from backend.models import FeatureGroup
from backend.services.online_store import query
from backend.services.plugins import get_plugin
from backend.services.templating import build_context

CTX = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))


def _setup(tmp_path, *, event_time_col="dt", watermark=None, kind="parquet"):
    s = Settings(storage_dir=str(tmp_path))
    s.ensure_dirs()
    engine = make_engine(s.db_path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        fg = FeatureGroup(project_id=None, name="g", entity_keys_json='["cust_no"]',
                          event_time_col=event_time_col, ttl_days=7, online_enabled=True,
                          offline_kind=kind, offline_location="g",
                          materialize_watermark=watermark)
        db.add(fg)
        db.commit()
        fgid = fg.id
    engine.dispose()
    return s, fgid, Session


def _write_parquet(env, rows_sql):
    out = env.offline_dir / "g"
    out.mkdir(parents=True, exist_ok=True)
    duckdb.sql(f"COPY ({rows_sql}) TO '{(out / 'p.parquet').as_posix()}' (FORMAT PARQUET)")


def test_materialize_parquet_full_then_incremental(tmp_path):
    env, fgid, Session = _setup(tmp_path)
    _write_parquet(env, "select 'C1' cust_no, '2026-06-10' dt, 1 v "
                        "union all select 'C2', '2026-06-11', 2")
    fn = get_plugin("materialize")
    r = fn({"feature_group_id": fgid}, CTX, env)
    assert r["rows"] == 2
    assert query(env.online_db_path, fgid, "C2")["payload"]["v"] == 2
    with Session() as db:
        assert db.get(FeatureGroup, fgid).materialize_watermark == datetime(2026, 6, 11)
    # 第二次:只有新数据进入
    _write_parquet(env, "select 'C3' cust_no, '2026-06-12' dt, 3 v "
                        "union all select 'C1', '2026-06-09', 99")
    r2 = fn({"feature_group_id": fgid}, CTX, env)
    assert r2["rows"] == 1  # 仅 C3(C1 的 06-09 早于水位)
    assert query(env.online_db_path, fgid, "C1")["payload"]["v"] == 1  # 未被旧数据覆盖


def test_materialize_requires_online_enabled(tmp_path):
    env, fgid, Session = _setup(tmp_path)
    with Session() as db:
        db.get(FeatureGroup, fgid).online_enabled = False
        db.commit()
    fn = get_plugin("materialize")
    with pytest.raises(ValueError, match="未启用在线"):
        fn({"feature_group_id": fgid}, CTX, env)


def test_materialize_missing_fg(tmp_path):
    env, _, _ = _setup(tmp_path)
    fn = get_plugin("materialize")
    with pytest.raises(ValueError, match="特征组不存在"):
        fn({"feature_group_id": 999}, CTX, env)


def test_materialize_warehouse_via_fetch(tmp_path, monkeypatch):
    env, fgid, Session = _setup(tmp_path, kind="warehouse")
    with Session() as db:
        db.get(FeatureGroup, fgid).offline_location = "dw.t_cust"
        db.commit()
    from backend.services.plugins import materialize as mat

    captured = {}

    def fake_fetch(conn_info, sql):
        captured["sql"] = sql
        return ["cust_no", "dt", "v"], [("C9", "2026-06-11", 7)]

    monkeypatch.setattr(mat, "_fetch_rows", fake_fetch)
    monkeypatch.setattr(mat, "_connection_info",
                        lambda params, env: ("mysql", "h", 3306, "u", "p", "dw"))
    fn = get_plugin("materialize")
    r = fn({"feature_group_id": fgid, "connection_id": 1}, CTX, env)
    assert r["rows"] == 1
    assert "dw.t_cust" in captured["sql"]
    assert query(env.online_db_path, fgid, "C9")["payload"]["v"] == 7


def test_midnight_watermark_no_rechurn(tmp_path):
    """datetime 数据 + 午夜水位:同时间戳行不被反复重灌(防字符串序死循环)。"""
    env, fgid, Session = _setup(tmp_path)
    _write_parquet(env, "select 'C1' cust_no, '2026-06-11 00:00:00' dt, 1 v")
    fn = get_plugin("materialize")
    r1 = fn({"feature_group_id": fgid}, CTX, env)
    assert r1["rows"] == 1
    r2 = fn({"feature_group_id": fgid}, CTX, env)
    assert r2["rows"] == 0  # 修复前这里会是 1(无限重灌)
