import pytest

from backend.services.dag import DagError, upstream_map, validate_dag


def _dag(nodes, edges):
    return {"nodes": nodes, "edges": edges}


N1 = {"key": "t1", "type": "duckdb_sql", "params": {"sql": "select 1"}}
N2 = {"key": "t2", "type": "python_script", "params": {"script": "s.py"}}
N3 = {"key": "t3", "type": "sql_pushdown", "params": {}}


def test_topo_order():
    order = validate_dag(_dag([N1, N2, N3], [["t1", "t2"], ["t2", "t3"]]))
    assert order == ["t1", "t2", "t3"]


def test_empty_nodes_rejected():
    with pytest.raises(DagError, match="至少需要一个节点"):
        validate_dag(_dag([], []))


def test_duplicate_key_rejected():
    with pytest.raises(DagError, match="重复"):
        validate_dag(_dag([N1, dict(N1)], []))


def test_unknown_type_rejected():
    with pytest.raises(DagError, match="类型非法"):
        validate_dag(_dag([{"key": "x", "type": "shell"}], []))


def test_edge_to_missing_node_rejected():
    with pytest.raises(DagError, match="不存在的节点"):
        validate_dag(_dag([N1], [["t1", "ghost"]]))


def test_self_loop_rejected():
    with pytest.raises(DagError, match="自环"):
        validate_dag(_dag([N1], [["t1", "t1"]]))


def test_cycle_rejected():
    with pytest.raises(DagError, match="存在环"):
        validate_dag(_dag([N1, N2], [["t1", "t2"], ["t2", "t1"]]))


def test_upstream_map():
    ups = upstream_map(_dag([N1, N2, N3], [["t1", "t3"], ["t2", "t3"]]))
    assert ups == {"t1": [], "t2": [], "t3": ["t1", "t2"]}
