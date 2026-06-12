import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_current_user, get_db, get_project_id
from ..models import Feature, FeatureGroup, LineageEdge, Workflow, WorkflowVersion
from ..services.audit import record

router = APIRouter(tags=["feature-groups"])

OFFLINE_KINDS = ("parquet", "warehouse")


class FeatureIn(BaseModel):
    name: str
    dtype: str = "double"
    description: str = ""


class FeatureGroupIn(BaseModel):
    name: str
    description: str = ""
    entity_keys: list[str]
    event_time_col: str | None = None
    ttl_days: int | None = None
    online_enabled: bool = False
    offline_kind: str = "parquet"
    offline_location: str
    workflow_id: int | None = None
    task_key: str | None = None
    features: list[FeatureIn]
    upstream_tables: list[str] = []


def _validate(db, body: FeatureGroupIn, pid: int) -> None:
    if not body.entity_keys:
        raise HTTPException(400, "至少一个主键列")
    if body.offline_kind not in OFFLINE_KINDS:
        raise HTTPException(400, f"离线落地须为 {OFFLINE_KINDS}")
    names = [f.name for f in body.features]
    if len(set(names)) != len(names):
        raise HTTPException(400, "特征名重复")
    if body.ttl_days is not None and body.ttl_days < 1:
        raise HTTPException(400, "TTL 须 ≥1 天")
    if body.online_enabled and not body.event_time_col:
        raise HTTPException(400, "启用在线服务须指定事件时间列(物化水位与 TTL 依赖它)")
    if (body.workflow_id is None) != (body.task_key is None):
        raise HTTPException(400, "workflow_id 与 task_key 须同时提供")
    if body.workflow_id is not None:
        wf = db.get(Workflow, body.workflow_id)
        if wf is None or wf.project_id != pid:
            raise HTTPException(400, "绑定的工作流不存在")
        ver = db.get(WorkflowVersion, wf.current_version_id)
        keys = [n["key"] for n in json.loads(ver.dag_json)["nodes"]] if ver else []
        if body.task_key not in keys:
            raise HTTPException(400, f"工作流中不存在节点 {body.task_key}")


def _fg_out(db, fg: FeatureGroup, with_children: bool = False) -> dict:
    out = {"id": fg.id, "name": fg.name, "version": fg.version,
           "description": fg.description, "entity_keys": json.loads(fg.entity_keys_json),
           "event_time_col": fg.event_time_col, "ttl_days": fg.ttl_days,
           "online_enabled": fg.online_enabled, "offline_kind": fg.offline_kind,
           "offline_location": fg.offline_location, "workflow_id": fg.workflow_id,
           "task_key": fg.task_key,
           "last_produced_at": fg.last_produced_at.isoformat() if fg.last_produced_at else None,
           "last_produced_rows": fg.last_produced_rows,
           "materialize_watermark": (fg.materialize_watermark.isoformat()
                                     if fg.materialize_watermark else None),
           "created_at": fg.created_at.isoformat()}
    if with_children:
        feats = db.scalars(select(Feature).where(Feature.feature_group_id == fg.id)
                           .order_by(Feature.id)).all()
        out["features"] = [{"name": f.name, "dtype": f.dtype, "description": f.description}
                           for f in feats]
        ups = db.scalars(select(LineageEdge).where(
            LineageEdge.dst == f"feature_group:{fg.id}")).all()
        out["upstream_tables"] = [e.src for e in ups]
    return out


def _get_in_project(db, fid: int, pid: int) -> FeatureGroup:
    fg = db.get(FeatureGroup, fid)
    if fg is None or fg.project_id != pid:
        raise HTTPException(404, "特征组不存在")
    return fg


def _insert_children(db, fg: FeatureGroup, body: FeatureGroupIn, pid: int) -> None:
    for f in body.features:
        db.add(Feature(feature_group_id=fg.id, name=f.name, dtype=f.dtype,
                       description=f.description))
    for src in body.upstream_tables:
        db.add(LineageEdge(project_id=pid, src=src, dst=f"feature_group:{fg.id}"))


@router.get("/feature-groups")
def list_feature_groups(all_versions: int = 0, db=Depends(get_db), pid=Depends(get_project_id)):
    rows = db.scalars(select(FeatureGroup).where(FeatureGroup.project_id == pid)
                      .order_by(FeatureGroup.name, FeatureGroup.version.desc())).all()
    if not all_versions:
        seen: set[str] = set()
        rows = [r for r in rows if not (r.name in seen or seen.add(r.name))]
    return [_fg_out(db, r) for r in rows]


@router.post("/feature-groups")
def create_feature_group(body: FeatureGroupIn, db=Depends(get_db),
                         user=Depends(get_current_user), pid=Depends(get_project_id)):
    _validate(db, body, pid)
    if db.scalar(select(FeatureGroup).where(FeatureGroup.project_id == pid,
                                            FeatureGroup.name == body.name).limit(1)):
        raise HTTPException(400, "特征组名已存在(更新请用 PUT)")
    fg = FeatureGroup(project_id=pid, name=body.name, description=body.description,
                      entity_keys_json=json.dumps(body.entity_keys, ensure_ascii=False),
                      event_time_col=body.event_time_col, ttl_days=body.ttl_days,
                      online_enabled=body.online_enabled, offline_kind=body.offline_kind,
                      offline_location=body.offline_location, owner_id=user.id,
                      workflow_id=body.workflow_id, task_key=body.task_key)
    db.add(fg)
    db.flush()
    _insert_children(db, fg, body, pid)
    record(db, user, "create_feature_group", body.name, project_id=pid)
    db.commit()
    return _fg_out(db, fg)


@router.get("/feature-groups/{fid}")
def get_feature_group(fid: int, db=Depends(get_db), pid=Depends(get_project_id)):
    return _fg_out(db, _get_in_project(db, fid, pid), with_children=True)


@router.put("/feature-groups/{fid}")
def update_feature_group(fid: int, body: FeatureGroupIn, db=Depends(get_db),
                         user=Depends(get_current_user), pid=Depends(get_project_id)):
    fg = _get_in_project(db, fid, pid)
    _validate(db, body, pid)
    head_ver = db.scalar(select(FeatureGroup.version)
                         .where(FeatureGroup.project_id == pid, FeatureGroup.name == fg.name)
                         .order_by(FeatureGroup.version.desc()).limit(1))
    if fg.version != head_ver:
        raise HTTPException(409, f"该特征组已有更新版本 v{head_ver},请基于最新版本修改")
    old = db.scalars(select(Feature).where(Feature.feature_group_id == fid)).all()
    old_schema = {(f.name, f.dtype) for f in old}
    new_schema = {(f.name, f.dtype) for f in body.features}
    if old_schema == new_schema:
        # 元数据更新,不升版本(描述/TTL/绑定/血缘可改)
        fg.description = body.description
        fg.event_time_col = body.event_time_col
        fg.ttl_days = body.ttl_days
        fg.online_enabled = body.online_enabled
        fg.offline_kind, fg.offline_location = body.offline_kind, body.offline_location
        fg.workflow_id, fg.task_key = body.workflow_id, body.task_key
        for f in old:  # 口径描述同步
            for nf in body.features:
                if nf.name == f.name:
                    f.description = nf.description
        db.query(LineageEdge).filter(LineageEdge.dst == f"feature_group:{fid}").delete()
        for src in body.upstream_tables:
            db.add(LineageEdge(project_id=pid, src=src, dst=f"feature_group:{fid}"))
        record(db, user, "update_feature_group", fg.name, project_id=pid)
        db.commit()
        return _fg_out(db, fg)
    # schema 变化 → 新版本行,旧版本并存
    max_ver = db.scalar(select(FeatureGroup.version)
                        .where(FeatureGroup.project_id == pid, FeatureGroup.name == fg.name)
                        .order_by(FeatureGroup.version.desc()).limit(1)) or 1
    new_fg = FeatureGroup(project_id=pid, name=fg.name, version=max_ver + 1,
                          description=body.description,
                          entity_keys_json=json.dumps(body.entity_keys, ensure_ascii=False),
                          event_time_col=body.event_time_col, ttl_days=body.ttl_days,
                          online_enabled=body.online_enabled, offline_kind=body.offline_kind,
                          offline_location=body.offline_location, owner_id=user.id,
                          workflow_id=body.workflow_id, task_key=body.task_key)
    db.add(new_fg)
    db.flush()
    _insert_children(db, new_fg, body, pid)
    record(db, user, "upgrade_feature_group", f"{fg.name} v{max_ver + 1}", project_id=pid)
    db.commit()
    return _fg_out(db, new_fg)


@router.get("/lineage")
def list_lineage(db=Depends(get_db), pid=Depends(get_project_id)):
    rows = db.scalars(select(LineageEdge).where(LineageEdge.project_id == pid)
                      .order_by(LineageEdge.id)).all()
    return [{"id": e.id, "src": e.src, "dst": e.dst} for e in rows]
