from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_current_user, get_db, get_settings, require_admin
from ..models import Connection
from ..services import connectors
from ..services.audit import record
from ..services.secrets import decrypt_text, encrypt_text

router = APIRouter(prefix="/connections", tags=["connections"])

CONN_TYPES = ("mysql", "spark")


class ConnectionIn(BaseModel):
    name: str
    conn_type: str
    host: str
    port: int
    username: str = ""
    password: str = ""
    database: str = ""


class ConnectionPatchIn(BaseModel):
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None
    database: str | None = None


def _conn_out(c: Connection) -> dict:
    return {"id": c.id, "name": c.name, "conn_type": c.conn_type, "host": c.host,
            "port": c.port, "username": c.username, "database": c.database,
            "has_password": bool(c.password_enc)}


@router.get("")
def list_connections(db=Depends(get_db), _=Depends(get_current_user)):
    """登录用户可见(配置任务时选用);密码不外发。"""
    return [_conn_out(c) for c in db.scalars(select(Connection).order_by(Connection.id)).all()]


@router.post("")
def create_connection(body: ConnectionIn, db=Depends(get_db),
                      settings=Depends(get_settings), admin=Depends(require_admin)):
    if body.conn_type not in CONN_TYPES:
        raise HTTPException(400, f"连接类型须为 {CONN_TYPES}")
    if db.scalar(select(Connection).where(Connection.name == body.name)):
        raise HTTPException(400, "连接名已存在")
    c = Connection(name=body.name, conn_type=body.conn_type, host=body.host, port=body.port,
                   username=body.username, database=body.database, created_by=admin.id,
                   password_enc=encrypt_text(body.password, settings.storage_dir) if body.password else "")
    db.add(c)
    record(db, admin, "create_connection", body.name)
    db.commit()
    return _conn_out(c)


@router.patch("/{cid}")
def patch_connection(cid: int, body: ConnectionPatchIn, db=Depends(get_db),
                     settings=Depends(get_settings), admin=Depends(require_admin)):
    c = db.get(Connection, cid)
    if c is None:
        raise HTTPException(404, "连接不存在")
    for field in ("host", "port", "username", "database"):
        v = getattr(body, field)
        if v is not None:
            setattr(c, field, v)
    if body.password is not None:
        c.password_enc = encrypt_text(body.password, settings.storage_dir) if body.password else ""
    record(db, admin, "update_connection", c.name)
    db.commit()
    return _conn_out(c)


@router.delete("/{cid}")
def delete_connection(cid: int, db=Depends(get_db), admin=Depends(require_admin)):
    c = db.get(Connection, cid)
    if c is None:
        raise HTTPException(404, "连接不存在")
    db.delete(c)
    record(db, admin, "delete_connection", c.name)
    db.commit()
    return {"ok": True}


@router.post("/{cid}/test")
def test_conn(cid: int, db=Depends(get_db), settings=Depends(get_settings), _=Depends(require_admin)):
    c = db.get(Connection, cid)
    if c is None:
        raise HTTPException(404, "连接不存在")
    password = decrypt_text(c.password_enc, settings.storage_dir) if c.password_enc else ""
    try:
        connectors.test_connection(c.conn_type, c.host, c.port, c.username, password, c.database)
    except Exception as e:  # noqa: BLE001  连通性探测失败统一转 400
        raise HTTPException(400, f"连接失败: {e}")
    return {"ok": True}
