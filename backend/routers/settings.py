from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_db, require_admin
from ..models import SystemSetting
from ..services.audit import record

router = APIRouter(prefix="/settings", tags=["settings"])

ALLOWED_KEYS = ("webhook_url", "quality_drop_ratio", "materialize_lag_hours",
                "tushare_token")


class SettingIn(BaseModel):
    value: str


@router.get("/{key}")
def get_setting(key: str, db=Depends(get_db), _=Depends(require_admin)):
    if key not in ALLOWED_KEYS:
        raise HTTPException(400, f"未知配置项,可选 {ALLOWED_KEYS}")
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    return {"key": key, "value": row.value if row else ""}


@router.put("/{key}")
def put_setting(key: str, body: SettingIn, db=Depends(get_db), admin=Depends(require_admin)):
    if key not in ALLOWED_KEYS:
        raise HTTPException(400, f"未知配置项,可选 {ALLOWED_KEYS}")
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    if row is None:
        db.add(SystemSetting(key=key, value=body.value))
    else:
        row.value = body.value
    record(db, admin, "update_setting", key)
    db.commit()
    return {"ok": True}
