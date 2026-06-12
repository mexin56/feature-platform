from backend.services.online_store import ensure_schema, query, upsert


def test_upsert_and_query(tmp_path):
    db = tmp_path / "online.db"
    ensure_schema(db)
    rows = [{"cust_no": "C1", "dt": "2026-06-11", "amt": 100.5},
            {"cust_no": "C2", "dt": "2026-06-11", "amt": 7}]
    n, max_et = upsert(db, fg_id=1, rows=rows, entity_keys=["cust_no"], event_time_col="dt")
    assert n == 2 and max_et == "2026-06-11"
    got = query(db, 1, "C1")
    assert got["payload"]["amt"] == 100.5
    assert got["event_time"] == "2026-06-11"
    assert got["updated_at"]
    assert query(db, 1, "GHOST") is None
    assert query(db, 2, "C1") is None  # 特征组隔离


def test_upsert_idempotent_overwrite(tmp_path):
    db = tmp_path / "online.db"
    ensure_schema(db)
    upsert(db, 1, [{"k": "a", "v": 1}], ["k"], None)
    n, max_et = upsert(db, 1, [{"k": "a", "v": 2}], ["k"], None)
    assert n == 1 and max_et is None
    assert query(db, 1, "a")["payload"]["v"] == 2  # 覆盖而非重复


def test_composite_entity_key(tmp_path):
    db = tmp_path / "online.db"
    ensure_schema(db)
    upsert(db, 1, [{"a": "x", "b": 1, "v": 9}], ["a", "b"], None)
    assert query(db, 1, "x|1")["payload"]["v"] == 9


def test_missing_entity_key_raises(tmp_path):
    import pytest

    db = tmp_path / "online.db"
    ensure_schema(db)
    with pytest.raises(ValueError, match="缺少主键列"):
        upsert(db, 1, [{"v": 1}], ["k"], None)


def test_entity_key_value_with_separator_rejected(tmp_path):
    import pytest

    db = tmp_path / "online.db"
    ensure_schema(db)
    with pytest.raises(ValueError, match="分隔符"):
        upsert(db, 1, [{"k": "a|b", "v": 1}], ["k"], None)
