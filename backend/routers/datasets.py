"""数据集目录 API:目录+可用性+market.duckdb 统计;一键生成线性采集工作流;
自定义数据集 CRUD(全局不分项目,key 冲突校验含内置目录)与测试拉取预览。
seed-workflow 复用 workflows.create_workflow 原路径(DAG/cron 校验、重名 400、审计)。"""
import json
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..deps import get_current_user, get_db, get_project_id, get_settings
from ..models import CustomDataset, SystemSetting
from ..services.audit import record
from ..services.collectors import CATALOG, available
from ..services.collectors.custom import COLLECTOR_TYPES, build_dataset
from .workflows import WorkflowIn, create_workflow

router = APIRouter(prefix="/datasets", tags=["datasets"])

DEFAULT_CRON = "0 17 * * 1-5"  # 工作日 17:00 收盘后采集
NODE_RETRIES, NODE_RETRY_DELAY_SEC, NODE_TIMEOUT_SEC = 1, 60, 1800
SLUG_RE = re.compile(r"^[a-z0-9_]{2,32}$")
MODES = ("snapshot", "per_symbol")
TEST_SYMBOL_CAP, TEST_PREVIEW_ROWS = 2, 5


def _market_stats(settings) -> dict[str, dict]:
    """market.duckdb 中全部 ods_ 表的 {table: {rows, max_dt}}:一次连接批量统计,
    再映射回目录(库不存在/正被写入/文件损坏返回空 dict,目录侧 stats=null)。"""
    p = Path(getattr(settings, "market_db", "") or "")
    if not str(p) or not p.exists():
        return {}
    import duckdb

    stats: dict[str, dict] = {}
    try:
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
    except duckdb.Error:  # 写入期间锁冲突/损坏库:目录降级为无统计
        return {}
    return stats


def _row_to_dataset(row: CustomDataset):
    """ORM 行 → (DataSet, 解析后的 config);config_json 损坏容错为 {}。"""
    try:
        config = json.loads(row.config_json or "{}")
    except ValueError:
        config = {}
    ds = build_dataset({"key": row.key, "source": row.source, "name": row.name,
                        "description": row.description, "mode": row.mode,
                        "collector_type": row.collector_type, "config": config,
                        "target_table": row.target_table})
    return ds, config


def _custom_out(row: CustomDataset) -> dict:
    """CRUD 接口返回的完整行。"""
    _, config = _row_to_dataset(row)
    return {"id": row.id, "key": row.key, "source": row.source,
            "dataset": row.dataset, "name": row.name,
            "description": row.description, "mode": row.mode,
            "collector_type": row.collector_type, "config": config,
            "target_table": row.target_table, "custom": True,
            "created_by": row.created_by,
            "created_at": row.created_at.isoformat() if row.created_at else None}


@router.get("")
def list_datasets(db=Depends(get_db), user=Depends(get_current_user),
                  pid=Depends(get_project_id), settings=Depends(get_settings)):
    stats = _market_stats(settings)
    out = []
    for ds in CATALOG.values():
        ok, reason = available(ds)
        out.append({"key": ds.key, "source": ds.source, "name": ds.name,
                    "module": ds.module, "desc": ds.desc, "mode": ds.mode,
                    "requires": ds.requires, "target_table": ds.target_table,
                    "available": ok, "reason": reason,
                    "stats": stats.get(ds.target_table)})
    for row in db.scalars(select(CustomDataset).order_by(CustomDataset.id)):
        ds, config = _row_to_dataset(row)
        ok, reason = available(ds)
        out.append({"key": ds.key, "source": ds.source, "name": ds.name,
                    "module": ds.module, "desc": ds.desc, "mode": ds.mode,
                    "requires": ds.requires, "target_table": ds.target_table,
                    "available": ok, "reason": reason,
                    "stats": stats.get(ds.target_table),
                    "custom": True, "id": row.id, "dataset": row.dataset,
                    "description": row.description,
                    "collector_type": row.collector_type, "config": config})
    return out


# ---------- 自定义数据集 CRUD ----------

class CustomDatasetIn(BaseModel):
    source: str
    dataset: str
    name: str
    description: str = ""
    mode: str
    collector_type: str
    config: dict


class CustomDatasetUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    mode: str | None = None
    collector_type: str | None = None
    config: dict | None = None


def _validate_custom(mode: str, collector_type: str, config: dict) -> None:
    if mode not in MODES:
        raise HTTPException(400, f"mode 仅支持 {MODES}")
    if collector_type not in COLLECTOR_TYPES:
        raise HTTPException(400, f"collector_type 仅支持 {COLLECTOR_TYPES}")
    if collector_type == "http_json" and not (config or {}).get("url"):
        raise HTTPException(400, "http_json 配置必须包含 url")
    if collector_type == "tushare_api" and not (config or {}).get("api_name"):
        raise HTTPException(400, "tushare_api 配置必须包含 api_name")


@router.post("/custom")
def create_custom(body: CustomDatasetIn, db=Depends(get_db),
                  user=Depends(get_current_user)):
    for slug in (body.source, body.dataset):
        if not SLUG_RE.match(slug or ""):
            raise HTTPException(400, "source/dataset 须为 2-32 位小写字母/数字/下划线")
    _validate_custom(body.mode, body.collector_type, body.config)
    key = f"{body.source}.{body.dataset}"
    if key in CATALOG or db.scalar(select(CustomDataset).where(CustomDataset.key == key)):
        raise HTTPException(400, f"数据集 key 已存在(含内置目录): {key}")
    row = CustomDataset(key=key, source=body.source, dataset=body.dataset,
                        name=body.name, description=body.description,
                        mode=body.mode, collector_type=body.collector_type,
                        config_json=json.dumps(body.config, ensure_ascii=False),
                        target_table=f"ods_{body.source}_{body.dataset}",
                        created_by=user.id)
    db.add(row)
    record(db, user, "create_custom_dataset", key)
    db.commit()
    db.refresh(row)
    return _custom_out(row)


@router.put("/custom/{cid}")
def update_custom(cid: int, body: CustomDatasetUpdate, db=Depends(get_db),
                  user=Depends(get_current_user)):
    row = db.get(CustomDataset, cid)
    if row is None:
        raise HTTPException(404, "自定义数据集不存在")
    mode = body.mode if body.mode is not None else row.mode
    ctype = (body.collector_type if body.collector_type is not None
             else row.collector_type)
    config = (body.config if body.config is not None
              else json.loads(row.config_json or "{}"))
    _validate_custom(mode, ctype, config)
    if body.name is not None:
        row.name = body.name
    if body.description is not None:
        row.description = body.description
    row.mode, row.collector_type = mode, ctype
    row.config_json = json.dumps(config, ensure_ascii=False)
    record(db, user, "update_custom_dataset", row.key)
    db.commit()
    db.refresh(row)
    return _custom_out(row)


@router.delete("/custom/{cid}")
def delete_custom(cid: int, db=Depends(get_db), user=Depends(get_current_user)):
    row = db.get(CustomDataset, cid)
    if row is None:
        raise HTTPException(404, "自定义数据集不存在")
    key = row.key
    db.delete(row)
    record(db, user, "delete_custom_dataset", key)
    db.commit()
    return {"ok": True}


# ---------- 测试拉取(真实网络;预览截 5 行,股票池截前 2 只) ----------

class CustomTestIn(BaseModel):
    collector_type: str
    config: dict
    mode: str = "snapshot"
    symbols: list[str] = []
    dt: str | None = None


@router.post("/custom/test")
def test_custom(body: CustomTestIn, db=Depends(get_db),
                user=Depends(get_current_user)):
    _validate_custom(body.mode, body.collector_type, body.config)
    ds = build_dataset({"key": "custom.test", "source": "custom", "name": "测试拉取",
                        "mode": body.mode, "collector_type": body.collector_type,
                        "config": body.config, "target_table": "ods_custom_test"})
    ok, reason = available(ds)
    if not ok:
        raise HTTPException(400, f"数据集不可用: {reason}")
    dt = body.dt or datetime.now().strftime("%Y-%m-%d")
    ctx = {"data_interval_end": dt}
    token = db.scalar(select(SystemSetting.value).where(
        SystemSetting.key == "tushare_token"))
    if token:
        ctx["tushare_token"] = token
    args = ({"symbols": [str(s) for s in body.symbols][:TEST_SYMBOL_CAP]}
            if body.mode == "per_symbol" else {})
    try:
        columns, rows = ds.fetch(args, ctx)
    except Exception as e:  # noqa: BLE001  拉取失败以可读 400 返回前端
        raise HTTPException(400, f"测试拉取失败: {e}")
    return {"columns": columns, "rows": [list(r) for r in rows[:TEST_PREVIEW_ROWS]],
            "row_count": len(rows)}


class SeedWorkflowIn(BaseModel):
    name: str
    cron: str = DEFAULT_CRON
    dataset_keys: list[str] = Field(min_length=1)
    symbols: list[str] = []
    interval_sec: float = 0.5


@router.post("/seed-workflow")
def seed_workflow(body: SeedWorkflowIn, db=Depends(get_db),
                  user=Depends(get_current_user), pid=Depends(get_project_id)):
    # 合并内置目录 + 自定义数据集(按 key 查找 DataSet)
    merged: dict[str, object] = dict(CATALOG)
    for row in db.scalars(select(CustomDataset)):
        if row.key not in merged:
            ds, _ = _row_to_dataset(row)
            merged[row.key] = ds
    unknown = [k for k in body.dataset_keys if k not in merged]
    if unknown:
        raise HTTPException(400, f"数据集不存在: {unknown}")
    unavailable = [k for k in body.dataset_keys if not available(merged[k])[0]]
    if unavailable:
        raise HTTPException(400, f"数据集不可用: {unavailable}")
    need_symbols = [k for k in body.dataset_keys if merged[k].mode == "per_symbol"]
    if need_symbols and not body.symbols:
        raise HTTPException(400, f"逐股数据集必须提供 symbols 股票池: {need_symbols}")
    nodes, edges, prev = [], [], None
    for k in body.dataset_keys:
        node_key = k.replace(".", "__")
        per_symbol = merged[k].mode == "per_symbol"
        args = ({"symbols": body.symbols, "interval_sec": body.interval_sec}
                if per_symbol else {})
        # 逐股节点随股票池动态放大超时(每股按 2 倍间隔留余量 + 10 分钟基量),
        # 下限维持 snapshot 的固定 1800s
        timeout_sec = (max(NODE_TIMEOUT_SEC,
                           int(len(body.symbols) * body.interval_sec * 2 + 600))
                       if per_symbol else NODE_TIMEOUT_SEC)
        nodes.append({"key": node_key, "type": "data_collect",
                      "params": {"dataset_key": k, "args": args},
                      "retries": NODE_RETRIES, "retry_delay_sec": NODE_RETRY_DELAY_SEC,
                      "timeout_sec": timeout_sec})
        if prev is not None:
            edges.append([prev, node_key])  # 按所选顺序线性串链,防限频
        prev = node_key
    wf_in = WorkflowIn(name=body.name, description=f"一键采集 {len(nodes)} 个数据集",
                       dag={"nodes": nodes, "edges": edges}, cron=body.cron,
                       timezone="Asia/Shanghai", catchup=False, concurrency_limit=1,
                       failure_policy="continue", alert_on_failure=True)
    out = create_workflow(wf_in, db=db, user=user, pid=pid)
    return {"id": out["id"], "version_no": out["version_no"], "task_count": len(nodes)}
