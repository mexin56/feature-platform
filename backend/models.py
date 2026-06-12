from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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

    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_member"),)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Connection(Base):
    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    # 连接类型:mysql / spark(Spark ThriftServer,PyHive)
    conn_type: Mapped[str] = mapped_column(String(16), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str] = mapped_column(String(128), default="")
    password_enc: Mapped[str] = mapped_column(Text, default="")  # Fernet 加密存储
    database: Mapped[str] = mapped_column(String(128), default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Workflow(Base):
    """工作流定义。修改 DAG 产生新 WorkflowVersion;实例持有版本快照。"""

    __tablename__ = "workflows"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_workflow_project_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    cron: Mapped[str | None] = mapped_column(String(64), nullable=True)  # None=仅手工触发
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    catchup: Mapped[bool] = mapped_column(Boolean, default=False)
    concurrency_limit: Mapped[int] = mapped_column(Integer, default=1)  # 同流最大并行实例数
    failure_policy: Mapped[str] = mapped_column(String(16), default="continue")  # continue/abort
    status: Mapped[str] = mapped_column(String(16), default="offline")  # online 才参与 cron 调度
    current_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # cron 水位
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"
    __table_args__ = (UniqueConstraint("workflow_id", "version_no", name="uq_wf_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    dag_json: Mapped[str] = mapped_column(Text, nullable=False)  # {"nodes":[...],"edges":[...]}
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WorkflowRun(Base):
    """工作流实例:一次触发。绑定 data_interval(Airflow 语义)与定义版本快照。"""

    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"), nullable=False)
    version_id: Mapped[int] = mapped_column(ForeignKey("workflow_versions.id"), nullable=False)
    run_type: Mapped[str] = mapped_column(String(16), nullable=False)  # scheduled/manual/backfill
    data_interval_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    data_interval_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="running")  # running/success/failed/stopped
    triggered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TaskInstance(Base):
    """任务实例:节点的一次执行。状态机见 spec §4.3;心跳供孤儿清理。"""

    __tablename__ = "task_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    task_key: Mapped[str] = mapped_column(String(64), nullable=False)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    params_json: Mapped[str] = mapped_column(Text, default="{}")  # 节点参数快照
    # none/scheduled/queued/running/success/failed/up_for_retry/upstream_failed/skipped
    state: Mapped[str] = mapped_column(String(20), default="none")
    try_number: Mapped[int] = mapped_column(Integer, default=0)
    max_tries: Mapped[int] = mapped_column(Integer, default=1)
    retry_delay_sec: Mapped[int] = mapped_column(Integer, default=60)
    timeout_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    log_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 插件产出(如行数)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
