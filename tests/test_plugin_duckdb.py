from datetime import datetime

import pytest

from backend.config import Settings
from backend.services.plugins import PluginError, get_plugin
from backend.services.templating import build_context

CTX = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))


def _env(tmp_path):
    s = Settings(storage_dir=str(tmp_path))
    s.ensure_dirs()
    return s


def test_unknown_plugin_raises():
    with pytest.raises(PluginError, match="未实现"):
        get_plugin("materialize")  # Phase 2 才实现
    with pytest.raises(PluginError, match="未知"):
        get_plugin("nonsense")


def test_duckdb_sql_returns_rows(tmp_path):
    fn = get_plugin("duckdb_sql")
    result = fn({"sql": "select 42 as answer union all select 43"}, CTX, _env(tmp_path))
    assert result["rows"] == 2
    assert result["output"] is None


def test_duckdb_sql_writes_parquet_with_template(tmp_path):
    env = _env(tmp_path)
    fn = get_plugin("duckdb_sql")
    result = fn({"sql": "select '{{ ds }}' as dt, 1 as v", "output_name": "daily_feat"}, CTX, env)
    assert result["rows"] == 1
    out = env.offline_dir / "daily_feat" / "20260611.parquet"
    assert out.exists()
    assert result["output"] == str(out)
    import duckdb

    assert duckdb.sql(f"select dt from read_parquet('{out.as_posix()}')").fetchone()[0] == "2026-06-11"


def test_duckdb_sql_bad_sql_raises(tmp_path):
    fn = get_plugin("duckdb_sql")
    with pytest.raises(Exception):
        fn({"sql": "select * from no_such_table"}, CTX, _env(tmp_path))
