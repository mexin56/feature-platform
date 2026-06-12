"""duckdb_sql 插件:本地 DuckDB 执行 SQL;配置 output_name 时产出 Parquet 特征快照。
SQL 可用 read_csv_auto/read_parquet 读本地文件;模板变量先渲染再执行。
market.duckdb 存在时只读挂载为 market 库,特征衍生可直接 select market.ods_xxx。"""
from ..collectors.writer import attach_market
from ..templating import render


def execute(params: dict, ctx: dict, env) -> dict:
    import duckdb

    sql = render(params["sql"], ctx)
    con = duckdb.connect()
    try:
        attach_market(con, env)
        rows = con.sql(f"select count(*) from ({sql})").fetchone()[0]
        output = None
        null_ratio = None
        distinct_keys = None
        if params.get("output_name"):
            out_dir = (env.offline_dir / params["output_name"]).resolve()
            try:
                out_dir.relative_to(env.offline_dir.resolve())
            except ValueError:
                raise ValueError(f"输出路径越界: {params['output_name']}")
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{ctx['ds_nodash']}.parquet"
            con.sql(f"COPY ({sql}) TO '{out_path.as_posix()}' (FORMAT PARQUET)")
            output = str(out_path)
            # 质量维度:全列平均空值率;提供 entity_keys 参数时计算主键去重数
            cols = [d[0] for d in con.sql(f"select * from ({sql}) limit 0").description]
            if cols and rows:
                null_exprs = " + ".join(
                    f"sum(case when \"{c}\" is null then 1 else 0 end)" for c in cols)
                total_nulls = con.sql(f"select {null_exprs} from ({sql})").fetchone()[0] or 0
                null_ratio = round(total_nulls / (rows * len(cols)), 6)
            ekeys = params.get("entity_keys")
            if ekeys and rows:
                key_expr = " || '|' || ".join(f'cast("{k}" as varchar)' for k in ekeys)
                distinct_keys = con.sql(
                    f"select count(distinct {key_expr}) from ({sql})").fetchone()[0]
        return {"rows": int(rows), "output": output,
                "null_ratio": null_ratio, "distinct_keys": distinct_keys}
    finally:
        con.close()
