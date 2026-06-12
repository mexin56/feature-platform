"""数据集目录 API:目录+可用性+market.duckdb 统计;一键生成线性采集工作流。
seed-workflow 复用 workflows.create_workflow 原路径(DAG/cron 校验、重名 400、审计)。"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..deps import get_current_user, get_db, get_project_id, get_settings
from ..services.collectors import CATALOG, available
from .workflows import WorkflowIn, create_workflow

router = APIRouter(prefix="/datasets", tags=["datasets"])

DEFAULT_CRON = "0 17 * * 1-5"  # 工作日 17:00 收盘后采集
NODE_RETRIES, NODE_RETRY_DELAY_SEC, NODE_TIMEOUT_SEC = 1, 60, 1800


def _market_stats(settings) -> dict[str, dict]:
    """market.duckdb 中全部 ods_ 表的 {table: {rows, max_dt}}:一次连接批量统计,
    再映射回目录(库不存在返回空 dict,目录侧 stats=null)。"""
    p = Path(getattr(settings, "market_db", "") or "")
    if not str(p) or not p.exists():
        return {}
    import duckdb

    stats: dict[str, dict] = {}
    con = duckdb.connect(str(p), read_only=True)
    try:
        tables = [r[0] for r in con.execute(
            "select table_name from information_schema.tables "
            "where table_schema='main' and table_name like 'ods_%'").fetchall()]
        for t in tables:  # 表名来自 information_schema 且 writer 已限 [a-z0-9_]
            try:
                rows, max_dt = con.execute(
                    f'select count(*), max(dt) from "{t}"').fetchone()
            except duckdb.Error:  # 缺 dt 列等异常表:跳过不拖垮目录
                continue
            stats[t] = {"rows": rows, "max_dt": max_dt}
    finally:
        con.close()
    return stats


@router.get("")
def list_datasets(user=Depends(get_current_user), pid=Depends(get_project_id),
                  settings=Depends(get_settings)):
    stats = _market_stats(settings)
    out = []
    for ds in CATALOG.values():
        ok, reason = available(ds)
        out.append({"key": ds.key, "source": ds.source, "name": ds.name,
                    "module": ds.module, "desc": ds.desc, "mode": ds.mode,
                    "requires": ds.requires, "target_table": ds.target_table,
                    "available": ok, "reason": reason,
                    "stats": stats.get(ds.target_table)})
    return out


class SeedWorkflowIn(BaseModel):
    name: str
    cron: str = DEFAULT_CRON
    dataset_keys: list[str] = Field(min_length=1)
    symbols: list[str] = []
    interval_sec: float = 0.5


@router.post("/seed-workflow")
def seed_workflow(body: SeedWorkflowIn, db=Depends(get_db),
                  user=Depends(get_current_user), pid=Depends(get_project_id)):
    unknown = [k for k in body.dataset_keys if k not in CATALOG]
    if unknown:
        raise HTTPException(400, f"数据集不存在: {unknown}")
    unavailable = [k for k in body.dataset_keys if not available(CATALOG[k])[0]]
    if unavailable:
        raise HTTPException(400, f"数据集不可用: {unavailable}")
    need_symbols = [k for k in body.dataset_keys if CATALOG[k].mode == "per_symbol"]
    if need_symbols and not body.symbols:
        raise HTTPException(400, f"逐股数据集必须提供 symbols 股票池: {need_symbols}")
    nodes, edges, prev = [], [], None
    for k in body.dataset_keys:
        node_key = k.replace(".", "__")
        args = ({"symbols": body.symbols, "interval_sec": body.interval_sec}
                if CATALOG[k].mode == "per_symbol" else {})
        nodes.append({"key": node_key, "type": "data_collect",
                      "params": {"dataset_key": k, "args": args},
                      "retries": NODE_RETRIES, "retry_delay_sec": NODE_RETRY_DELAY_SEC,
                      "timeout_sec": NODE_TIMEOUT_SEC})
        if prev is not None:
            edges.append([prev, node_key])  # 按所选顺序线性串链,防限频
        prev = node_key
    wf_in = WorkflowIn(name=body.name, description=f"一键采集 {len(nodes)} 个数据集",
                       dag={"nodes": nodes, "edges": edges}, cron=body.cron,
                       timezone="Asia/Shanghai", catchup=False, concurrency_limit=1,
                       failure_policy="continue", alert_on_failure=True)
    out = create_workflow(wf_in, db=db, user=user, pid=pid)
    return {"id": out["id"], "version_no": out["version_no"], "task_count": len(nodes)}
