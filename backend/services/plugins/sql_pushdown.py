"""sql_pushdown 插件:渲染后的 SQL 下推到 Spark ThriftServer / MySQL 源端执行。
params: {connection_id, sql, count_sql?, expect_rows_min?}
分号分隔多语句逐条执行;count_sql 用于产出行数统计与下限校验(0 行防呆)。"""
from ..templating import render


def _exec_statements(conn_type, host, port, username, password, database, statements):
    if conn_type == "mysql":
        import pymysql

        conn = pymysql.connect(host=host, port=port, user=username, password=password,
                               database=database or None, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                for s in statements:
                    cur.execute(s)
            conn.commit()
        finally:
            conn.close()
    elif conn_type == "spark":
        from pyhive import hive

        conn = hive.connect(host=host, port=port, username=username or None,
                            database=database or "default")
        try:
            cur = conn.cursor()
            for s in statements:
                cur.execute(s)
        finally:
            conn.close()
    else:
        raise ValueError(f"不支持的连接类型: {conn_type}")


def _exec_scalar(conn_type, host, port, username, password, database, sql):
    if conn_type == "mysql":
        import pymysql

        conn = pymysql.connect(host=host, port=port, user=username, password=password,
                               database=database or None, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                return cur.fetchone()[0]
        finally:
            conn.close()
    elif conn_type == "spark":
        from pyhive import hive

        conn = hive.connect(host=host, port=port, username=username or None,
                            database=database or "default")
        try:
            cur = conn.cursor()
            cur.execute(sql)
            return cur.fetchone()[0]
        finally:
            conn.close()
    raise ValueError(f"不支持的连接类型: {conn_type}")


def _connection_info(params: dict, env) -> tuple:
    from sqlalchemy.orm import sessionmaker

    from ...db import make_engine
    from ...models import Connection
    from ..secrets import decrypt_text

    engine = make_engine(env.db_path)
    try:
        Session = sessionmaker(bind=engine)
        with Session() as db:
            c = db.get(Connection, params["connection_id"])
            if c is None:
                raise ValueError("连接不存在")
            password = decrypt_text(c.password_enc, env.storage_dir) if c.password_enc else ""
            return (c.conn_type, c.host, c.port, c.username, password, c.database)
    finally:
        engine.dispose()


def execute(params: dict, ctx: dict, env) -> dict:
    info = _connection_info(params, env)
    sql = render(params["sql"], ctx)
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    _exec_statements(*info, statements)
    rows = None
    if params.get("count_sql"):
        rows = int(_exec_scalar(*info, render(params["count_sql"], ctx)))
        if params.get("expect_rows_min") is not None and rows < int(params["expect_rows_min"]):
            raise RuntimeError(f"产出行数 {rows} 低于下限 {params['expect_rows_min']}")
    return {"rows": rows}
