"""DAG(节点+边 JSON)校验:key 唯一、类型合法、边引用存在、无环(Kahn)。"""

TASK_TYPES = ("sql_pushdown", "duckdb_sql", "python_script", "materialize", "dependent")


class DagError(ValueError):
    pass


def validate_dag(dag: dict) -> list[str]:
    """校验 DAG 并返回拓扑序 key 列表;非法抛 DagError。"""
    nodes = dag.get("nodes") or []
    edges = dag.get("edges") or []
    if not nodes:
        raise DagError("DAG 至少需要一个节点")
    keys = [n.get("key") for n in nodes]
    if any(not k for k in keys):
        raise DagError("节点 key 不能为空")
    if len(set(keys)) != len(keys):
        raise DagError("节点 key 重复")
    for n in nodes:
        if n.get("type") not in TASK_TYPES:
            raise DagError(f"节点 {n['key']} 类型非法: {n.get('type')}")
    key_set = set(keys)
    for e in edges:
        if not isinstance(e, (list, tuple)) or len(e) != 2 or e[0] not in key_set or e[1] not in key_set:
            raise DagError(f"边引用不存在的节点: {e}")
        if e[0] == e[1]:
            raise DagError(f"不允许自环: {e}")
    downstream: dict[str, list[str]] = {k: [] for k in keys}
    indeg = {k: 0 for k in keys}
    for a, b in edges:
        downstream[a].append(b)
        indeg[b] += 1
    queue = [k for k in keys if indeg[k] == 0]
    order: list[str] = []
    while queue:
        k = queue.pop(0)
        order.append(k)
        for d in downstream[k]:
            indeg[d] -= 1
            if indeg[d] == 0:
                queue.append(d)
    if len(order) != len(keys):
        raise DagError("DAG 存在环")
    return order


def upstream_map(dag: dict) -> dict[str, list[str]]:
    """每个节点的直接上游 key 列表(调度器依赖推进用)。"""
    ups: dict[str, list[str]] = {n["key"]: [] for n in dag.get("nodes") or []}
    for a, b in dag.get("edges") or []:
        ups[b].append(a)
    return ups
