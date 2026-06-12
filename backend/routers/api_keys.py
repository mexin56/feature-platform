import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_db, require_admin
from ..models import ApiKey
from ..services.audit import record

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class ApiKeyIn(BaseModel):
    name: str


def _out(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "is_active": k.is_active, "calls": k.calls,
            "created_at": k.created_at.isoformat()}


@router.get("")
def list_keys(db=Depends(get_db), _=Depends(require_admin)):
    return [_out(k) for k in db.scalars(select(ApiKey).order_by(ApiKey.id)).all()]


@router.post("")
def create_key(body: ApiKeyIn, db=Depends(get_db), admin=Depends(require_admin)):
    if db.scalar(select(ApiKey).where(ApiKey.name == body.name)):
        raise HTTPException(400, "名称已存在")
    plaintext = secrets.token_urlsafe(32)
    k = ApiKey(name=body.name, key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
               created_by=admin.id)
    db.add(k)
    record(db, admin, "create_api_key", body.name)
    db.commit()
    return {**_out(k), "key": plaintext}  # 明文仅此一次


@router.post("/{kid}/disable")
def disable_key(kid: int, db=Depends(get_db), admin=Depends(require_admin)):
    k = db.get(ApiKey, kid)
    if k is None:
        raise HTTPException(404, "不存在")
    k.is_active = False
    record(db, admin, "disable_api_key", k.name)
    db.commit()
    return {"ok": True}
