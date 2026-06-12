"""data_collect 插件:按数据集目录采集并幂等写入 market.duckdb。
params: {dataset_key, args?: {symbols?, interval_sec?, ...}}
dt = ctx.data_interval_end 的日期(手动/无调度上下文时取当天)。"""
from datetime import datetime


def execute(params: dict, ctx: dict, env) -> dict:
    from ..collectors import CATALOG, available
    from ..collectors.writer import write_market

    key = params.get("dataset_key") or ""
    ds = CATALOG.get(key)
    if ds is None:
        raise RuntimeError(f"数据集不存在: {key}")
    ok, reason = available(ds)
    if not ok:
        raise RuntimeError(f"数据集不可用: {key}({reason})")
    end = ctx.get("data_interval_end")
    dt = end[:10] if end else datetime.now().strftime("%Y-%m-%d")
    columns, rows = ds.fetch(params.get("args") or {}, ctx)
    n = write_market(env, ds.target_table, dt, columns, rows)
    return {"table": ds.target_table, "rows": n, "dt": dt}
