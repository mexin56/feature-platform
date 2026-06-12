from datetime import datetime

import pytest

from backend.services.templating import build_context, render


def test_build_context():
    ctx = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))
    assert ctx["ds"] == "2026-06-11"
    assert ctx["ds_nodash"] == "20260611"
    assert ctx["data_interval_start"] == "2026-06-11 00:00:00"
    assert ctx["data_interval_end"] == "2026-06-12 00:00:00"


def test_render_replaces_with_spaces():
    ctx = build_context(datetime(2026, 6, 11), datetime(2026, 6, 12))
    sql = "insert overwrite t partition(dt='{{ ds }}') select * from s where etl_date='{{ds}}'"
    out = render(sql, ctx)
    assert out.count("2026-06-11") == 2
    assert "{{" not in out


def test_unknown_variable_raises():
    with pytest.raises(ValueError, match="未知模板变量"):
        render("select '{{ nope }}'", {"ds": "x"})


def test_no_template_passthrough():
    assert render("select 1", {"ds": "x"}) == "select 1"
