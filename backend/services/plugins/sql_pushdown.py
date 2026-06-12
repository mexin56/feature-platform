"""sql_pushdown 插件:渲染后的 SQL 下推到 Spark ThriftServer / MySQL 源端执行。
params: {connection_id, sql, count_sql?, expect_rows_min?}
sql 可为字符串(整体作为一条语句执行)或字符串列表(逐条执行)。
多语句请用列表显式声明,避免字符串字面量中分号被误切。
count_sql 用于产出行数统计与下限校验(0 行防呆)。"""
from ..templating import render


def _exec_statements(conn_type, host, port, username, password, database, statements):
    if conn_type == "mysql":
        import pymysql

        conn = pymysql.connect(host=host, port=port, user=username, password=password,
                               database=database or None, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                for i, s in enumerate(statements):
                    try:
                        cur.execute(s)
                    except Exception as e:
                        raise RuntimeError(f"第 {i + 1} 条语句执行失败: {s[:200]}") from e
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    elif conn_type == "spark":
        # ThriftServer 内网常用 NONE/NOSASL 认证,暂不传 password;LDAP 场景后续按需支持
        from pyhive import hive

        conn = hive.connect(host=host, port=port, username=username or None,
                            database=database or "default")
        try:
            cur = conn.cursor()
            try:
                for i, s in enumerate(statements):
                    try:
                        cur.execute(s)
                    except Exception as e:
                        raise RuntimeError(f"第 {i + 1} 条语句执行失败: {s[:200]}") from e
            finally:
                cur.close()
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
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("count_sql 未返回任何行")
                return row[0]
        finally:
            conn.close()
    elif conn_type == "spark":
        # ThriftServer 内网常用 NONE/NOSASL 认证,暂不传 password;LDAP 场景后续按需支持
        from pyhive import hive

        conn = hive.connect(host=host, port=port, username=username or None,
                            database=database or "default")
        try:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("count_sql 未返回任何行")
                return row[0]
            finally:
                cur.close()
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
    raw = params["sql"]
    statements = [render(s, ctx) for s in raw] if isinstance(raw, list) else [render(raw, ctx)]
    _exec_statements(*info, statements)
    rows = None
    if params.get("count_sql"):
        rows = int(_exec_scalar(*info, render(params["count_sql"], ctx)))
        if params.get("expect_rows_min") is not None and rows < int(params["expect_rows_min"]):
            raise RuntimeError(f"产出行数 {rows} 低于下限 {params['expect_rows_min']}")
    return {"rows": rows}
