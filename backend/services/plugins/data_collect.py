"""data_collect 插件:按数据集目录采集并幂等写入 market.duckdb。
params: {dataset_key, args?: {symbols?, interval_sec?, ...}}
dt = ctx.data_interval_end 的日期(手动/无调度上下文时取当天)。
token 数据集:SystemSetting(tushare_token)经 ctx 注入采集器(子进程无 ORM 会话,
插件运行于 task_runner 进程内,用 stdlib sqlite3 只读 meta.db)。"""
from datetime import datetime


def _system_setting(db_path, key: str) -> str | None:
    """读取一条系统配置;库/表不存在或值为空返回 None(不阻断采集,回退默认 token)。"""
    import sqlite3
    from pathlib import Path

    if not Path(str(db_path)).exists():
        return None
    try:
        con = sqlite3.connect(str(db_path))
        try:
            row = con.execute("select value from system_settings where key = ?",
                              (key,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return row[0] if row and row[0] else None


def execute(params: dict, ctx: dict, env) -> dict:
    from ..collectors import CATALOG, available
    from ..collectors.custom import resolve_custom
    from ..collectors.writer import write_market

    key = params.get("dataset_key") or ""
    # override/custom 优先:用户配置永远优先于内置 CATALOG
    ds = resolve_custom(key, env.db_path)
    if ds is None:
        ds = CATALOG.get(key)
    if ds is None:
        raise RuntimeError(f"数据集不存在: {key}")
    ok, reason = available(ds)
    if not ok:
        raise RuntimeError(f"数据集不可用: {key}({reason})")
    if ds.requires == "token" and not ctx.get("tushare_token"):
        token = _system_setting(env.db_path, "tushare_token")
        if token:
            ctx["tushare_token"] = token
    end = ctx.get("data_interval_end")
    dt = end[:10] if end else datetime.now().strftime("%Y-%m-%d")
    columns, rows = ds.fetch(params.get("args") or {}, ctx)
    n = write_market(env, ds.target_table, dt, columns, rows)
    return {"table": ds.target_table, "rows": n, "dt": dt}
