# Phase 1a:项目骨架与后端核心 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭起 feature-platform 后端骨架:配置/数据库/用户-项目-审计域模型/JWT 认证/用户管理/项目隔离,可登录可建项目。

**Architecture:** FastAPI 单体 + SQLite(WAL),Settings 注入 storage 目录(测试用 tmp_path 隔离),与 D:\ml-platform 同模式但账号独立。首次启动自动播种 admin/admin123。

**Tech Stack:** Python 3.10(conda scpy310)、FastAPI、SQLAlchemy 2.x、bcrypt、python-jose、cryptography(Fernet)、pytest + httpx(TestClient)。

**环境与命令约定:** 所有命令在 `D:\feature-platform` 下、conda scpy310 环境中执行(`conda activate scpy310`)。

---

### Task 1: 项目骨架与配置

**Files:**
- Create: `requirements.txt`
- Create: `.gitignore`
- Create: `backend/__init__.py`(空文件)
- Create: `backend/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: 写失败测试**

`tests/test_config.py`:

```python
from backend.config import Settings


def test_settings_dirs(tmp_path):
    s = Settings(storage_dir=str(tmp_path))
    s.ensure_dirs()
    assert s.db_path == tmp_path / "meta.db"
    assert s.online_db_path == tmp_path / "online_store.db"
    assert (tmp_path / "offline").is_dir()
    assert (tmp_path / "logs").is_dir()
    assert (tmp_path / "scripts").is_dir()
    assert s.sync_scheduler is False
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'backend'`

- [ ] **Step 3: 写实现**

`requirements.txt`:

```
fastapi
uvicorn[standard]
sqlalchemy>=2.0
bcrypt
python-jose[cryptography]
cryptography
croniter
duckdb
pandas
pyarrow
pymysql
pytest
httpx
```

`.gitignore`:

```
__pycache__/
*.pyc
storage/
node_modules/
frontend/dist/
.pytest_cache/
```

`backend/__init__.py`:空文件。

`backend/config.py`:

```python
import os
from pathlib import Path


class Settings:
    """运行时配置。storage_dir 可注入,测试用 tmp_path 隔离。"""

    def __init__(self, storage_dir: str | None = None, sync_scheduler: bool = False):
        base = Path(__file__).resolve().parent.parent
        self.storage_dir = Path(
            storage_dir or os.environ.get("FEATURE_PLATFORM_STORAGE", base / "storage")
        )
        self.offline_dir = self.storage_dir / "offline"
        self.logs_dir = self.storage_dir / "logs"
        self.scripts_dir = self.storage_dir / "scripts"
        self.db_path = self.storage_dir / "meta.db"
        self.online_db_path = self.storage_dir / "online_store.db"
        # 测试模式:不启动 tick 线程,由测试手动驱动调度循环(Phase 1b 使用)
        self.sync_scheduler = sync_scheduler

    def ensure_dirs(self) -> None:
        for d in (self.offline_dir, self.logs_dir, self.scripts_dir):
            d.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS(1 passed)

- [ ] **Step 5: Commit**

```bash
git add requirements.txt .gitignore backend/ tests/
git commit -m "feat: 项目骨架与 Settings 配置"
```

---

### Task 2: 数据库基建与域模型(用户/项目/成员/审计)

**Files:**
- Create: `backend/db.py`
- Create: `backend/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: 写失败测试**

`tests/test_models.py`:

```python
from sqlalchemy.orm import sessionmaker

from backend.db import Base, make_engine
from backend.models import AuditLog, Project, ProjectMember, User


def test_create_all_and_insert(tmp_path):
    engine = make_engine(tmp_path / "meta.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        u = User(username="alice", password_hash="x", role="developer")
        db.add(u)
        db.flush()
        p = Project(name="反欺诈特征", description="", owner_id=u.id)
        db.add(p)
        db.flush()
        db.add(ProjectMember(project_id=p.id, user_id=u.id))
        db.add(AuditLog(project_id=p.id, user_id=u.id, action="create_project", detail="反欺诈特征"))
        db.commit()
        assert db.query(User).one().is_active is True
        assert db.query(Project).one().owner_id == u.id
        assert db.query(AuditLog).one().action == "create_project"


def test_wal_mode(tmp_path):
    engine = make_engine(tmp_path / "meta.db")
    with engine.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
    assert mode == "wal"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL,`ModuleNotFoundError`(backend.db / backend.models 不存在)

- [ ] **Step 3: 写实现**

`backend/db.py`:

```python
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def make_engine(db_path):
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")  # 调度器线程+子进程并发写库防锁
        cur.close()

    return engine


def ensure_column(engine, table: str, column: str, ddl: str) -> None:
    """SQLite 轻量迁移:列不存在则 ALTER TABLE 补列(兼容既有库)。"""
    with engine.connect() as conn:
        cols = [r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})")]
        if column not in cols:
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            conn.commit()
```

`backend/models.py`:

```python
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    # 角色:admin 管理员 / developer 开发者 / viewer 只读
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="developer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ProjectMember(Base):
    __tablename__ = "project_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_models.py -v`
Expected: PASS(2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/models.py tests/test_models.py
git commit -m "feat: SQLite 基建(WAL/轻量迁移)与用户-项目-审计模型"
```

---

### Task 3: 密钥与认证服务(bcrypt + JWT)

**Files:**
- Create: `backend/services/__init__.py`(空文件)
- Create: `backend/services/secrets.py`
- Create: `backend/services/auth.py`
- Create: `tests/test_auth_service.py`

- [ ] **Step 1: 写失败测试**

`tests/test_auth_service.py`:

```python
from backend.services.auth import (
    create_token, decode_token, hash_password, verify_password,
)
from backend.services.secrets import secret_key


def test_password_hash_roundtrip():
    h = hash_password("s3cret!")
    assert h != "s3cret!"
    assert verify_password("s3cret!", h) is True
    assert verify_password("wrong", h) is False
    assert verify_password("anything", "not-a-hash") is False


def test_secret_key_persistent(tmp_path):
    k1 = secret_key(tmp_path)
    k2 = secret_key(tmp_path)
    assert k1 == k2  # 同目录重复获取一致(落盘持久化)
    assert (tmp_path / ".secret_key").exists()


def test_token_roundtrip(tmp_path):
    token = create_token(42, tmp_path)
    assert decode_token(token, tmp_path) == 42
    assert decode_token("garbage", tmp_path) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_auth_service.py -v`
Expected: FAIL,`ModuleNotFoundError`

- [ ] **Step 3: 写实现**

`backend/services/__init__.py`:空文件。

`backend/services/secrets.py`:

```python
"""平台密钥:storage/.secret_key,首次生成 Fernet key 并落盘。
同一密钥用于 JWT 签名与连接密码加密(Phase 1b)。"""
from pathlib import Path

from cryptography.fernet import Fernet


def secret_key(storage_dir: Path) -> bytes:
    storage_dir = Path(storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    f = storage_dir / ".secret_key"
    if not f.exists():
        f.write_bytes(Fernet.generate_key())
    return f.read_bytes()
```

`backend/services/auth.py`:

```python
"""认证:bcrypt 密码哈希 + JWT(密钥复用 storage/.secret_key)。"""
from datetime import datetime, timedelta
from pathlib import Path

ALGO = "HS256"
TOKEN_HOURS = 12


def hash_password(plaintext: str) -> str:
    import bcrypt

    return bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode()


def verify_password(plaintext: str, hashed: str) -> bool:
    import bcrypt

    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), hashed.encode())
    except ValueError:
        return False


def create_token(user_id: int, storage_dir: Path) -> str:
    from jose import jwt

    from .secrets import secret_key

    payload = {
        "sub": str(user_id),
        "exp": datetime.utcnow() + timedelta(hours=TOKEN_HOURS),
    }
    return jwt.encode(payload, secret_key(storage_dir).decode(), algorithm=ALGO)


def decode_token(token: str, storage_dir: Path) -> int | None:
    from jose import JWTError, jwt

    from .secrets import secret_key

    try:
        return int(
            jwt.decode(token, secret_key(storage_dir).decode(), algorithms=[ALGO])["sub"]
        )
    except (JWTError, KeyError, ValueError):
        return None
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_auth_service.py -v`
Expected: PASS(3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/services/ tests/test_auth_service.py
git commit -m "feat: Fernet 平台密钥与 bcrypt+JWT 认证服务"
```

---

### Task 4: 应用工厂 create_app + 测试夹具 + 健康检查 + admin 播种

**Files:**
- Create: `backend/app.py`
- Create: `run.py`
- Create: `tests/conftest.py`
- Create: `tests/test_app.py`

- [ ] **Step 1: 写失败测试**

`tests/conftest.py`:

```python
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings


@pytest.fixture()
def client(tmp_path):
    app = create_app(Settings(storage_dir=str(tmp_path), sync_scheduler=True))
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_headers(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}
```

`tests/test_app.py`:

```python
from sqlalchemy import select

from backend.app import create_app
from backend.config import Settings
from backend.models import User


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_seed_admin_once(tmp_path):
    """首次启动播种 admin/admin123;重复启动不重复播种。"""
    settings = Settings(storage_dir=str(tmp_path), sync_scheduler=True)
    app = create_app(settings)
    app2 = create_app(settings)  # 第二次启动
    with app.state.sessionmaker() as db:
        users = db.scalars(select(User)).all()
    assert len(users) == 1
    assert users[0].username == "admin"
    assert users[0].role == "admin"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_app.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'backend.app'`

- [ ] **Step 3: 写实现**

`backend/app.py`:

```python
from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from .config import Settings
from .db import Base, make_engine


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_dirs()
    engine = make_engine(settings.db_path)

    app = FastAPI(title="特征调度管理平台")
    app.state.settings = settings
    app.state.sessionmaker = sessionmaker(bind=engine)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    from . import models  # noqa: F401  确保模型注册

    Base.metadata.create_all(engine)
    _seed_admin(app.state.sessionmaker)
    return app


def _seed_admin(SessionLocal) -> None:
    """首次启动播种默认管理员 admin/admin123(请登录后立即改密)。"""
    from sqlalchemy import func, select

    from .models import User
    from .services.auth import hash_password

    with SessionLocal() as db:
        if (db.scalar(select(func.count(User.id))) or 0) == 0:
            db.add(User(username="admin", password_hash=hash_password("admin123"), role="admin"))
            db.commit()
```

`run.py`:

```python
"""一键启动:conda scpy310 下 python run.py,浏览器访问 http://localhost:8100"""
import uvicorn

from backend.app import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8100)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_app.py -v`
Expected: 此时 test_health 仍 FAIL(conftest 的 admin_headers 不影响它,但 login 路由未实现——test_health 本身应 PASS,admin_headers 夹具只在被引用时才执行)。确认:`test_health PASS`、`test_seed_admin_once PASS`。

- [ ] **Step 5: Commit**

```bash
git add backend/app.py run.py tests/conftest.py tests/test_app.py
git commit -m "feat: 应用工厂+健康检查+admin 播种+测试夹具"
```

---

### Task 5: 依赖注入与登录 API

**Files:**
- Create: `backend/deps.py`
- Create: `backend/routers/__init__.py`(空文件)
- Create: `backend/routers/auth.py`
- Modify: `backend/app.py`(挂载 auth 路由)
- Create: `tests/test_login.py`

- [ ] **Step 1: 写失败测试**

`tests/test_login.py`:

```python
def test_login_ok(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    body = r.json()
    assert body["token"]
    assert body["user"]["username"] == "admin"
    assert body["user"]["role"] == "admin"


def test_login_wrong_password(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_me_requires_token(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_ok(client, admin_headers):
    r = client.get("/api/auth/me", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["username"] == "admin"


def test_change_password(client, admin_headers):
    r = client.post(
        "/api/auth/change-password",
        json={"old_password": "admin123", "new_password": "newpass1"},
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert client.post(
        "/api/auth/login", json={"username": "admin", "password": "newpass1"}
    ).status_code == 200
    assert client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    ).status_code == 401
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_login.py -v`
Expected: FAIL,404(路由不存在)

- [ ] **Step 3: 写实现**

`backend/deps.py`:

```python
from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select


def get_db(request: Request):
    SessionLocal = request.app.state.sessionmaker
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_settings(request: Request):
    return request.app.state.settings


def get_current_user(request: Request, db=Depends(get_db)):
    """JWT 认证;viewer 只读控制(非 GET 拒绝)。"""
    from .models import User
    from .services.auth import decode_token

    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "未登录")
    uid = decode_token(header[7:], request.app.state.settings.storage_dir)
    if uid is None:
        raise HTTPException(401, "登录已过期,请重新登录")
    user = db.get(User, uid)
    if user is None or not user.is_active:
        raise HTTPException(401, "账号不存在或已禁用")
    if user.role == "viewer" and request.method not in ("GET", "HEAD", "OPTIONS"):
        raise HTTPException(403, "只读角色无权执行此操作")
    return user


def require_admin(user=Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


def get_project_id(request: Request, db=Depends(get_db), user=Depends(get_current_user)):
    """当前项目(X-Project-Id 头);校验成员资格。admin 全部可见。"""
    from .models import ProjectMember

    raw = request.headers.get("x-project-id", "").strip()
    if not raw:
        raise HTTPException(400, "缺少 X-Project-Id")
    try:
        pid = int(raw)
    except ValueError:
        raise HTTPException(400, "X-Project-Id 非法")
    if user.role != "admin":
        member = db.scalar(
            select(func.count(ProjectMember.id)).where(
                ProjectMember.project_id == pid, ProjectMember.user_id == user.id
            )
        )
        if not member:
            raise HTTPException(403, "不是该项目成员")
    return pid
```

`backend/routers/__init__.py`:空文件。

`backend/routers/auth.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_current_user, get_db, get_settings
from ..models import User
from ..services.auth import create_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


def user_out(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role, "is_active": u.is_active}


@router.post("/login")
def login(body: LoginIn, db=Depends(get_db), settings=Depends(get_settings)):
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    return {"token": create_token(user.id, settings.storage_dir), "user": user_out(user)}


@router.get("/me")
def me(user=Depends(get_current_user)):
    return user_out(user)


@router.post("/change-password")
def change_password(body: ChangePasswordIn, user=Depends(get_current_user), db=Depends(get_db)):
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "原密码错误")
    if len(body.new_password) < 6:
        raise HTTPException(400, "新密码至少 6 位")
    db.get(User, user.id).password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}
```

`backend/app.py` 修改——在 `Base.metadata.create_all(engine)` 之前插入路由挂载:

```python
    from .routers import auth as auth_router

    app.include_router(auth_router.router, prefix="/api")
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_login.py -v`
Expected: PASS(5 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/deps.py backend/routers/ backend/app.py tests/test_login.py
git commit -m "feat: JWT 登录/改密 API 与权限依赖注入"
```

---

### Task 6: 用户管理 API(管理员)

**Files:**
- Create: `backend/routers/users.py`
- Modify: `backend/app.py`(挂载 users 路由)
- Create: `tests/test_users.py`

- [ ] **Step 1: 写失败测试**

`tests/test_users.py`:

```python
def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_admin_create_list_user(client, admin_headers):
    r = client.post(
        "/api/users",
        json={"username": "bob", "password": "bob123456", "role": "developer"},
        headers=admin_headers,
    )
    assert r.status_code == 200
    names = [u["username"] for u in client.get("/api/users", headers=admin_headers).json()]
    assert "bob" in names


def test_duplicate_username_rejected(client, admin_headers):
    client.post("/api/users", json={"username": "bob", "password": "bob123456", "role": "developer"}, headers=admin_headers)
    r = client.post("/api/users", json={"username": "bob", "password": "x2345678", "role": "viewer"}, headers=admin_headers)
    assert r.status_code == 400


def test_non_admin_cannot_manage_users(client, admin_headers):
    client.post("/api/users", json={"username": "bob", "password": "bob123456", "role": "developer"}, headers=admin_headers)
    bob = _login(client, "bob", "bob123456")
    assert client.post("/api/users", json={"username": "eve", "password": "e1234567", "role": "viewer"}, headers=bob).status_code == 403


def test_disable_and_reset(client, admin_headers):
    uid = client.post(
        "/api/users", json={"username": "bob", "password": "bob123456", "role": "developer"}, headers=admin_headers
    ).json()["id"]
    # 重置密码
    assert client.post(f"/api/users/{uid}/reset-password", json={"new_password": "reset123"}, headers=admin_headers).status_code == 200
    assert client.post("/api/auth/login", json={"username": "bob", "password": "reset123"}).status_code == 200
    # 禁用后无法登录
    assert client.patch(f"/api/users/{uid}", json={"is_active": False}, headers=admin_headers).status_code == 200
    assert client.post("/api/auth/login", json={"username": "bob", "password": "reset123"}).status_code == 401


def test_viewer_readonly_enforced(client, admin_headers):
    client.post("/api/users", json={"username": "ro", "password": "ro123456", "role": "viewer"}, headers=admin_headers)
    ro = _login(client, "ro", "ro123456")
    # viewer 可 GET
    assert client.get("/api/auth/me", headers=ro).status_code == 200
    # viewer 任何写操作被拒(403 在 get_current_user 层)
    assert client.post("/api/auth/change-password", json={"old_password": "ro123456", "new_password": "x1234567"}, headers=ro).status_code == 403
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_users.py -v`
Expected: FAIL,404(/api/users 不存在)

- [ ] **Step 3: 写实现**

`backend/routers/users.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_db, require_admin
from ..models import User
from ..services.auth import hash_password
from .auth import user_out

router = APIRouter(prefix="/users", tags=["users"])

ROLES = ("admin", "developer", "viewer")


class UserCreateIn(BaseModel):
    username: str
    password: str
    role: str = "developer"


class UserPatchIn(BaseModel):
    role: str | None = None
    is_active: bool | None = None


class ResetPasswordIn(BaseModel):
    new_password: str


@router.get("")
def list_users(db=Depends(get_db), _=Depends(require_admin)):
    return [user_out(u) for u in db.scalars(select(User).order_by(User.id)).all()]


@router.post("")
def create_user(body: UserCreateIn, db=Depends(get_db), _=Depends(require_admin)):
    if body.role not in ROLES:
        raise HTTPException(400, f"角色须为 {ROLES}")
    if len(body.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    if db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(400, "用户名已存在")
    u = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(u)
    db.commit()
    return user_out(u)


@router.patch("/{user_id}")
def patch_user(user_id: int, body: UserPatchIn, db=Depends(get_db), admin=Depends(require_admin)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "用户不存在")
    if u.id == admin.id and body.is_active is False:
        raise HTTPException(400, "不能禁用自己")
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(400, f"角色须为 {ROLES}")
        u.role = body.role
    if body.is_active is not None:
        u.is_active = body.is_active
    db.commit()
    return user_out(u)


@router.post("/{user_id}/reset-password")
def reset_password(user_id: int, body: ResetPasswordIn, db=Depends(get_db), _=Depends(require_admin)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "用户不存在")
    if len(body.new_password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    u.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}
```

`backend/app.py` 修改——路由挂载处增加:

```python
    from .routers import users as users_router

    app.include_router(users_router.router, prefix="/api")
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_users.py -v`
Expected: PASS(5 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/routers/users.py backend/app.py tests/test_users.py
git commit -m "feat: 用户管理 API(创建/角色/禁用/重置密码,仅管理员)"
```

---

### Task 7: 项目 API(隔离 + 成员 + 审计留痕)

**Files:**
- Create: `backend/services/audit.py`
- Create: `backend/routers/projects.py`
- Modify: `backend/app.py`(挂载 projects 路由)
- Create: `tests/test_projects.py`

- [ ] **Step 1: 写失败测试**

`tests/test_projects.py`:

```python
def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_user(client, admin_headers, name, role="developer"):
    client.post("/api/users", json={"username": name, "password": name + "123456", "role": role}, headers=admin_headers)
    return _login(client, name, name + "123456")


def test_create_project_owner_is_member(client, admin_headers):
    bob = _mk_user(client, admin_headers, "bob")
    r = client.post("/api/projects", json={"name": "反欺诈特征", "description": "d"}, headers=bob)
    assert r.status_code == 200
    pid = r.json()["id"]
    mine = client.get("/api/projects", headers=bob).json()
    assert [p["id"] for p in mine] == [pid]


def test_project_isolation(client, admin_headers):
    bob = _mk_user(client, admin_headers, "bob")
    eve = _mk_user(client, admin_headers, "eve")
    client.post("/api/projects", json={"name": "p1", "description": ""}, headers=bob)
    assert client.get("/api/projects", headers=eve).json() == []   # 非成员不可见
    assert len(client.get("/api/projects", headers=admin_headers).json()) == 1  # admin 全见


def test_member_management(client, admin_headers):
    bob = _mk_user(client, admin_headers, "bob")
    eve = _mk_user(client, admin_headers, "eve")
    pid = client.post("/api/projects", json={"name": "p1", "description": ""}, headers=bob).json()["id"]
    eve_id = next(u["id"] for u in client.get("/api/users", headers=admin_headers).json() if u["username"] == "eve")
    # bob(owner)加 eve 为成员
    assert client.post(f"/api/projects/{pid}/members", json={"user_id": eve_id}, headers=bob).status_code == 200
    assert len(client.get("/api/projects", headers=eve).json()) == 1
    # eve(非 owner)不能管理成员
    bob_id = next(u["id"] for u in client.get("/api/users", headers=admin_headers).json() if u["username"] == "bob")
    assert client.delete(f"/api/projects/{pid}/members/{bob_id}", headers=eve).status_code == 403
    # bob 移除 eve
    assert client.delete(f"/api/projects/{pid}/members/{eve_id}", headers=bob).status_code == 200
    assert client.get("/api/projects", headers=eve).json() == []


def test_audit_logged(client, admin_headers):
    bob = _mk_user(client, admin_headers, "bob")
    pid = client.post("/api/projects", json={"name": "p1", "description": ""}, headers=bob).json()["id"]
    logs = client.get(f"/api/projects/{pid}/audit", headers=bob).json()
    assert logs[0]["action"] == "create_project"
    assert logs[0]["username"] == "bob"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_projects.py -v`
Expected: FAIL,404(/api/projects 不存在)

- [ ] **Step 3: 写实现**

`backend/services/audit.py`:

```python
"""操作留痕:关键动作写 audit_logs(调用方负责 commit)。"""
from ..models import AuditLog


def record(db, user, action: str, detail: str = "", project_id: int | None = None) -> None:
    db.add(AuditLog(project_id=project_id, user_id=user.id if user else None,
                    action=action, detail=detail))
```

`backend/routers/projects.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import get_current_user, get_db
from ..models import AuditLog, Project, ProjectMember, User
from ..services.audit import record

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectIn(BaseModel):
    name: str
    description: str = ""


class MemberIn(BaseModel):
    user_id: int


def _project_out(p: Project) -> dict:
    return {"id": p.id, "name": p.name, "description": p.description,
            "owner_id": p.owner_id, "created_at": p.created_at.isoformat()}


def _require_owner_or_admin(db, pid: int, user) -> Project:
    p = db.get(Project, pid)
    if p is None:
        raise HTTPException(404, "项目不存在")
    if user.role != "admin" and p.owner_id != user.id:
        raise HTTPException(403, "仅项目负责人或管理员可操作")
    return p


def _require_member_or_admin(db, pid: int, user) -> Project:
    p = db.get(Project, pid)
    if p is None:
        raise HTTPException(404, "项目不存在")
    if user.role != "admin":
        ok = db.scalar(select(ProjectMember).where(
            ProjectMember.project_id == pid, ProjectMember.user_id == user.id))
        if not ok:
            raise HTTPException(403, "不是该项目成员")
    return p


@router.get("")
def list_projects(db=Depends(get_db), user=Depends(get_current_user)):
    q = select(Project).order_by(Project.id)
    if user.role != "admin":
        q = q.join(ProjectMember, ProjectMember.project_id == Project.id).where(
            ProjectMember.user_id == user.id)
    return [_project_out(p) for p in db.scalars(q).all()]


@router.post("")
def create_project(body: ProjectIn, db=Depends(get_db), user=Depends(get_current_user)):
    if user.role == "viewer":
        raise HTTPException(403, "只读角色不能创建项目")
    if db.scalar(select(Project).where(Project.name == body.name)):
        raise HTTPException(400, "项目名已存在")
    p = Project(name=body.name, description=body.description, owner_id=user.id)
    db.add(p)
    db.flush()
    db.add(ProjectMember(project_id=p.id, user_id=user.id))
    record(db, user, "create_project", body.name, project_id=p.id)
    db.commit()
    return _project_out(p)


@router.post("/{pid}/members")
def add_member(pid: int, body: MemberIn, db=Depends(get_db), user=Depends(get_current_user)):
    _require_owner_or_admin(db, pid, user)
    if db.get(User, body.user_id) is None:
        raise HTTPException(404, "用户不存在")
    exists = db.scalar(select(ProjectMember).where(
        ProjectMember.project_id == pid, ProjectMember.user_id == body.user_id))
    if not exists:
        db.add(ProjectMember(project_id=pid, user_id=body.user_id))
        record(db, user, "add_member", f"user_id={body.user_id}", project_id=pid)
        db.commit()
    return {"ok": True}


@router.delete("/{pid}/members/{user_id}")
def remove_member(pid: int, user_id: int, db=Depends(get_db), user=Depends(get_current_user)):
    p = _require_owner_or_admin(db, pid, user)
    if user_id == p.owner_id:
        raise HTTPException(400, "不能移除项目负责人")
    m = db.scalar(select(ProjectMember).where(
        ProjectMember.project_id == pid, ProjectMember.user_id == user_id))
    if m:
        db.delete(m)
        record(db, user, "remove_member", f"user_id={user_id}", project_id=pid)
        db.commit()
    return {"ok": True}


@router.get("/{pid}/audit")
def list_audit(pid: int, db=Depends(get_db), user=Depends(get_current_user)):
    _require_member_or_admin(db, pid, user)
    rows = db.execute(
        select(AuditLog, User.username).join(User, AuditLog.user_id == User.id, isouter=True)
        .where(AuditLog.project_id == pid).order_by(AuditLog.id.desc()).limit(200)
    ).all()
    return [{"id": a.id, "action": a.action, "detail": a.detail,
             "username": name, "created_at": a.created_at.isoformat()}
            for a, name in rows]
```

`backend/app.py` 修改——路由挂载处增加:

```python
    from .routers import projects as projects_router

    app.include_router(projects_router.router, prefix="/api")
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_projects.py -v`
Expected: PASS(4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/services/audit.py backend/routers/projects.py backend/app.py tests/test_projects.py
git commit -m "feat: 项目隔离/成员管理/审计留痕 API"
```

---

### Task 8: 全量回归与启动冒烟

**Files:**
- 无新文件

- [ ] **Step 1: 全量测试**

Run: `python -m pytest tests/ -v`
Expected: 全部 PASS(约 20 个),无 warning 级别以上问题

- [ ] **Step 2: 启动冒烟**

Run: `python -c "from backend.app import create_app; app = create_app(); print('routes:', len(app.routes))"`
Expected: 输出 `routes: <数字>` 且无异常(默认 storage/ 目录被创建,meta.db 生成,admin 播种)

- [ ] **Step 3: 清理冒烟产物并确认 .gitignore 生效**

Run: `git status --short`
Expected: 输出为空(storage/ 已被 .gitignore 排除;若有未跟踪文件,检查 .gitignore)

- [ ] **Step 4: Commit(如有遗漏文件)**

```bash
git add -A
git commit -m "chore: phase1a 收尾" --allow-empty
```

---

## Self-Review 记录

- **Spec 覆盖**:本计划只覆盖 spec §2(部分:FastAPI/SQLite/认证层)与 §8 权限部分;调度内核/特征域/在线服务/监控/前端由 Phase 1b–4 计划覆盖(见 roadmap)。
- **占位符**:无 TBD/TODO;所有步骤含完整代码与命令。
- **类型一致性**:`user_out` 定义于 routers/auth.py 并被 users.py 复用;`Settings(storage_dir, sync_scheduler)` 全文一致;角色字符串统一 `admin/developer/viewer`。
