"""在线特征存储:独立 SQLite(WAL)KV 表,stdlib sqlite3 直连。
entity_key = 多主键值按 '|' 拼接;payload 存整行 JSON;event_time 为 ISO 字符串(口径见计划头)。"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

_schema_ensured: set[str] = set()


def _connect(path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def ensure_schema(path) -> None:
    if str(path) in _schema_ensured:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = _connect(path)
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS online_features(
                feature_group_id INTEGER NOT NULL,
                entity_key TEXT NOT NULL,
                payload TEXT NOT NULL,
                event_time TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(feature_group_id, entity_key))
        """)
        con.commit()
    finally:
        con.close()
    _schema_ensured.add(str(path))


def build_entity_key(row: dict, entity_keys: list[str]) -> str:
    parts = []
    for k in entity_keys:
        if k not in row or row[k] is None:
            raise ValueError(f"缺少主键列: {k}")
        if "|" in str(row[k]):
            raise ValueError(f"主键值不能包含分隔符 '|': {k}={row[k]}")
        parts.append(str(row[k]))
    return "|".join(parts)


def upsert(path, fg_id: int, rows: list[dict], entity_keys: list[str],
           event_time_col: str | None) -> tuple[int, str | None]:
    """幂等写入(INSERT OR REPLACE)。返回 (写入行数, 本批最大 event_time 字符串或 None)。"""
    ensure_schema(path)
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    max_et: str | None = None
    con = _connect(path)
    try:
        for r in rows:
            ek = build_entity_key(r, entity_keys)
            et = None
            if event_time_col and r.get(event_time_col) is not None:
                et = str(r[event_time_col])
                if max_et is None or et > max_et:
                    max_et = et
            con.execute(
                "INSERT OR REPLACE INTO online_features VALUES (?,?,?,?,?)",
                (fg_id, ek, json.dumps(r, ensure_ascii=False, default=str), et, now))
        con.commit()
    finally:
        con.close()
    return len(rows), max_et


def query(path, fg_id: int, entity_key: str) -> dict | None:
    ensure_schema(path)
    con = _connect(path)
    try:
        row = con.execute(
            "SELECT payload, event_time, updated_at FROM online_features "
            "WHERE feature_group_id=? AND entity_key=?", (fg_id, entity_key)).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    return {"payload": json.loads(row[0]), "event_time": row[1], "updated_at": row[2]}
