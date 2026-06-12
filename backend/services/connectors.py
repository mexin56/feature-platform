"""数据源连接器:测试连通。各任务插件自行取数,本模块只负责连通性探测。"""


def test_connection(conn_type: str, host: str, port: int, username: str,
                    password: str, database: str) -> None:
    """连通失败抛异常;成功返回 None。"""
    if conn_type == "mysql":
        import pymysql

        c = pymysql.connect(host=host, port=port, user=username, password=password,
                            database=database or None, connect_timeout=5)
        c.close()
    elif conn_type == "spark":
        from pyhive import hive

        c = hive.connect(host=host, port=port, username=username or None,
                         database=database or "default")
        c.close()
    else:
        raise ValueError(f"不支持的连接类型: {conn_type}")
