"""duckdb_sql 插件:本地 DuckDB 执行 SQL;配置 output_name 时产出 Parquet 特征快照。
SQL 可用 read_csv_auto/read_parquet 读本地文件;模板变量先渲染再执行。"""
from ..templating import render


def execute(params: dict, ctx: dict, env) -> dict:
    import duckdb

    sql = render(params["sql"], ctx)
    con = duckdb.connect()
    try:
        rows = con.sql(f"select count(*) from ({sql})").fetchone()[0]
        output = None
        if params.get("output_name"):
            out_dir = env.offline_dir / params["output_name"]
            if not str(out_dir.resolve()).startswith(str(env.offline_dir.resolve())):
                raise ValueError(f"输出路径越界: {params['output_name']}")
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{ctx['ds_nodash']}.parquet"
            con.sql(f"COPY ({sql}) TO '{out_path.as_posix()}' (FORMAT PARQUET)")
            output = str(out_path)
        return {"rows": int(rows), "output": output}
    finally:
        con.close()
