"""在线特征查询:/online-features 供线上系统(X-API-Key);
/feature-groups/{fid}/online-debug 供平台用户调试(JWT+项目成员)。共享查询逻辑。"""
import hashlib
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_db, get_project_id, get_settings
from ..models import ApiKey, FeatureGroup
from ..services.online_store import build_entity_key, query

router = APIRouter(tags=["online"])


class OnlineQueryIn(BaseModel):
    feature_group_id: int
    keys: list[dict]


class DebugQueryIn(BaseModel):
    keys: list[dict]


def _query_fg(db, settings, fg: FeatureGroup, keys: list[dict]) -> list[dict]:
    if not fg.online_enabled:
        raise HTTPException(400, "该特征组未启用在线服务")
    entity_keys = json.loads(fg.entity_keys_json)
    now = datetime.utcnow()
    results = []
    for k in keys:
        try:
            ek = build_entity_key(k, entity_keys)
        except ValueError as e:
            raise HTTPException(400, str(e))
        row = query(settings.online_db_path, fg.id, ek)
        if row is None:
            results.append({"key": k, "values": None, "expired": False})
            continue
        expired = False
        if fg.ttl_days and row["event_time"]:
            et = _parse_dt(row["event_time"])
            if et is not None and et < now - timedelta(days=fg.ttl_days):
                expired = True
        results.append({"key": k, "values": None if expired else row["payload"],
                        "expired": expired,
                        "event_time": row["event_time"], "updated_at": row["updated_at"]})
    return results


def _parse_dt(s: str):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


@router.post("/online-features")
def online_features(body: OnlineQueryIn, db=Depends(get_db), settings=Depends(get_settings),
                    x_api_key: str | None = Header(default=None)):
    if not x_api_key:
        raise HTTPException(401, "缺少 X-API-Key")
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    ak = db.scalar(select(ApiKey).where(ApiKey.key_hash == key_hash,
                                        ApiKey.is_active.is_(True)))
    if ak is None:
        raise HTTPException(401, "API Key 无效或已禁用")
    fg = db.get(FeatureGroup, body.feature_group_id)
    if fg is None:
        raise HTTPException(404, "特征组不存在")
    results = _query_fg(db, settings, fg, body.keys)
    ak.calls += 1
    db.commit()
    return {"feature_group": fg.name, "version": fg.version, "results": results}


@router.post("/feature-groups/{fid}/online-debug")
def online_debug(fid: int, body: DebugQueryIn, db=Depends(get_db),
                 settings=Depends(get_settings), pid=Depends(get_project_id)):
    fg = db.get(FeatureGroup, fid)
    if fg is None or fg.project_id != pid:
        raise HTTPException(404, "特征组不存在")
    return {"feature_group": fg.name, "version": fg.version,
            "results": _query_fg(db, settings, fg, body.keys)}
