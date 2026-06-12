import json
from datetime import datetime

import pytest
from sqlalchemy.orm import sessionmaker

from backend.config import Settings
from backend.db import Base, make_engine
from backend.models import Connection
from backend.services.plugins import get_plugin
from backend.services.secrets import encrypt_text
from backend.services.templating import build_context

CTX = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))


def _env_with_conn(tmp_path):
    s = Settings(storage_dir=str(tmp_path))
    s.ensure_dirs()
    engine = make_engine(s.db_path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(Connection(name="dw", conn_type="spark", host="h", port=10000,
                          username="u", password_enc=encrypt_text("pw", s.storage_dir),
                          database="dw"))
        db.commit()
        cid = db.query(Connection).one().id
    engine.dispose()
    return s, cid


def test_pushdown_executes_rendered_statements(tmp_path, monkeypatch):
    env, cid = _env_with_conn(tmp_path)
    calls = {}

    def fake_exec(conn_type, host, port, username, password, database, statements):
        calls.update(locals())

    from backend.services.plugins import sql_pushdown

    monkeypatch.setattr(sql_pushdown, "_exec_statements", fake_exec)
    fn = get_plugin("sql_pushdown")
    result = fn({"connection_id": cid,
                 "sql": "insert overwrite t partition(dt='{{ ds }}') select 1; analyze table t"},
                CTX, env)
    assert calls["conn_type"] == "spark"
    assert calls["password"] == "pw"  # 解密后传递
    assert calls["statements"] == [
        "insert overwrite t partition(dt='2026-06-11') select 1", "analyze table t"]
    assert result["rows"] is None  # 未配置 count_sql


def test_pushdown_count_and_min_guard(tmp_path, monkeypatch):
    env, cid = _env_with_conn(tmp_path)
    from backend.services.plugins import sql_pushdown

    monkeypatch.setattr(sql_pushdown, "_exec_statements", lambda *a: None)
    monkeypatch.setattr(sql_pushdown, "_exec_scalar", lambda *a: 5)
    fn = get_plugin("sql_pushdown")
    result = fn({"connection_id": cid, "sql": "select 1",
                 "count_sql": "select count(*) from t where dt='{{ ds }}'",
                 "expect_rows_min": 1}, CTX, env)
    assert result["rows"] == 5
    with pytest.raises(RuntimeError, match="低于下限"):
        fn({"connection_id": cid, "sql": "select 1",
            "count_sql": "select count(*) from t", "expect_rows_min": 10}, CTX, env)


def test_pushdown_missing_connection(tmp_path):
    env, _ = _env_with_conn(tmp_path)
    fn = get_plugin("sql_pushdown")
    with pytest.raises(ValueError, match="连接不存在"):
        fn({"connection_id": 999, "sql": "select 1"}, CTX, env)
