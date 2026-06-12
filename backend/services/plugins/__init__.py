"""任务插件注册表。插件签名:execute(params: dict, ctx: dict, env) -> dict
params=节点参数快照;ctx=模板变量上下文;env=Settings(取 offline_dir/scripts_dir)。
返回 dict 存入 TaskInstance.result_json。"""
from ..dag import TASK_TYPES


class PluginError(ValueError):
    pass


def get_plugin(task_type: str):
    if task_type == "duckdb_sql":
        from .duckdb_sql import execute

        return execute
    if task_type == "python_script":
        from .python_script import execute

        return execute
    if task_type == "sql_pushdown":
        from .sql_pushdown import execute

        return execute
    if task_type == "dependent":
        from .dependent import execute

        return execute
    if task_type == "materialize":
        from .materialize import execute

        return execute
    if task_type == "data_collect":
        from .data_collect import execute

        return execute
    if task_type in TASK_TYPES:
        raise PluginError(f"插件未实现(后续阶段提供): {task_type}")
    raise PluginError(f"未知任务类型: {task_type}")
