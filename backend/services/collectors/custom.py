"""自定义数据集执行器:按 custom_datasets 行构建 DataSet(不进 CATALOG)。
http_json:httpx 拉 JSON,records_path 点路径取记录,field_map 列映射(空=首条键排序);
tushare_api:必须经 tushare_client.get_pro(),per_symbol 时 {symbol} 先经 _ts_code 归一。
模板变量 {dt} {dt_nodash} {symbol} 作用于 url/headers/params/body 的字符串值(递归)。
resolve_custom 供 data_collect 插件 stdlib sqlite3 直读 meta.db(子进程无 ORM 会话)。"""
import json

import httpx

from . import _common as c
from .base import DataSet
from .tushare_client import get_pro
from .tushare_src import _ts_code

TIMEOUT = 20
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
COLLECTOR_TYPES = ("http_json", "tushare_api")


def _render(value, vars: dict):
    """模板替换:字符串内 {dt}/{dt_nodash}/{symbol};dict/list 递归,其余原样。"""
    if isinstance(value, str):
        for k, v in vars.items():
            value = value.replace("{" + k + "}", str(v))
        return value
    if isinstance(value, dict):
        return {k: _render(v, vars) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, vars) for v in value]
    return value


def _base_vars(ctx: dict) -> dict:
    dt = c.ctx_dt(ctx)
    return {"dt": dt, "dt_nodash": c.nodash(dt)}


def _walk_records(payload, path: str) -> list[dict]:
    """点路径取记录列表:缺失/类型不符容错为 [];非 dict 记录跳过。"""
    cur = payload
    for part in [p for p in str(path or "").split(".") if p]:
        if not isinstance(cur, dict):
            return []
        cur = cur.get(part)
    if not isinstance(cur, list):
        return []
    return [r for r in cur if isinstance(r, dict)]


def _cell(v):
    """记录值 → duckdb 安全标量:标量原样,非标量 json.dumps 落字符串。"""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return json.dumps(v, ensure_ascii=False)


def _fields(records: list[dict], field_map: dict | None) -> tuple[list[str], list[str]]:
    """(列名, 取值 JSON 键):field_map={列: JSON键};空 = 首条记录键排序自动映射。"""
    if field_map:
        cols = list(field_map.keys())
        return cols, [field_map[col] for col in cols]
    if not records:
        return [], []
    keys = sorted(records[0].keys())
    return c.safe_columns(keys), keys


def _http_call(config: dict, vars: dict) -> list[dict]:
    """一次 HTTP 调用 → 记录列表。"""
    url = _render(config.get("url") or "", vars)
    method = str(config.get("method") or "GET").upper()
    headers = {**UA, **_render(config.get("headers") or {}, vars)}
    params = _render(config.get("params") or {}, vars)
    body = config.get("body")
    r = httpx.request(method, url, params=params or None,
                      json=_render(body, vars) if body is not None else None,
                      headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    return _walk_records(r.json(), config.get("records_path") or "")


def exec_http_json(config: dict, args: dict, ctx: dict,
                   mode: str = "snapshot") -> tuple[list[str], list[tuple]]:
    """http_json 执行:snapshot 一次调用;per_symbol 逐股循环(限频 sleep,
    行首前置 symbol 列)。"""
    base = _base_vars(ctx)
    fm = config.get("field_map") or None
    if mode != "per_symbol":
        records = _http_call(config, base)
        cols, keys = _fields(records, fm)
        return cols, [tuple(_cell(r.get(k)) for k in keys) for r in records]
    columns: list[str] | None = None
    keys: list[str] = []
    rows: list[tuple] = []
    for sym in c.iter_symbols(args):
        records = _http_call(config, {**base, "symbol": str(sym)})
        if not records:
            continue
        if columns is None:
            columns, keys = _fields(records, fm)
        rows.extend((str(sym),) + tuple(_cell(r.get(k)) for k in keys)
                    for r in records)
    return ["symbol"] + (columns or []), rows


def exec_tushare(config: dict, args: dict, ctx: dict,
                 mode: str = "snapshot") -> tuple[list[str], list[tuple]]:
    """tushare_api 执行:pro = get_pro(ctx.tushare_token);params 渲染后调
    pro.{api_name}(**params[, fields]);per_symbol 时 {symbol} 经 _ts_code 归一,
    DataFrame 经 _common 转换(NaN→None),行首前置 symbol 列。"""
    pro = get_pro((ctx or {}).get("tushare_token") or None)
    api = getattr(pro, str(config.get("api_name") or ""))
    fields = config.get("fields") or ""
    base = _base_vars(ctx)

    def call(vars: dict):
        kw = dict(_render(config.get("params") or {}, vars))
        if fields:
            kw["fields"] = fields
        return api(**kw)

    if mode != "per_symbol":
        return c.df_to_table(call(base))
    return c.per_symbol_df(args, lambda sym: call({**base, "symbol": _ts_code(sym)}))


_EXECUTORS = {"http_json": exec_http_json, "tushare_api": exec_tushare}


def build_dataset(row: dict) -> DataSet:
    """custom_datasets 行(config 已解析为 dict)→ DataSet。
    http_json 恒可用;tushare_api 复用 requires=token 语义(仅需 tushare 包)。"""
    ctype = row.get("collector_type") or ""
    exec_fn = _EXECUTORS.get(ctype)
    if exec_fn is None:
        raise ValueError(f"未知采集器类型: {ctype}")
    key = row["key"]
    mode = row.get("mode") or "snapshot"
    config = row.get("config") or {}

    def fetch(args, ctx):
        return exec_fn(config, args or {}, ctx or {}, mode=mode)

    return DataSet(
        key=key, source=row.get("source") or key.split(".", 1)[0],
        name=row.get("name") or key,
        module="tushare" if ctype == "tushare_api" else "httpx",
        desc=row.get("description") or "", mode=mode,
        requires="token" if ctype == "tushare_api" else None,
        target_table=row.get("target_table") or "ods_" + key.replace(".", "_"),
        fetch=fetch)


def resolve_custom(key: str, db_path) -> DataSet | None:
    """stdlib sqlite3 直读 meta.db custom_datasets(库/表缺失或未命中返回 None)。"""
    import sqlite3
    from pathlib import Path

    if not key or not Path(str(db_path)).exists():
        return None
    try:
        con = sqlite3.connect(str(db_path))
        try:
            row = con.execute(
                "select key, source, name, description, mode, collector_type, "
                "config_json, target_table from custom_datasets where key = ?",
                (key,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        config = json.loads(row[6] or "{}")
    except ValueError:
        config = {}
    return build_dataset({"key": row[0], "source": row[1], "name": row[2],
                          "description": row[3], "mode": row[4],
                          "collector_type": row[5], "config": config,
                          "target_table": row[7]})
