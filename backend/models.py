from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    alert_on_failure: Mapped[bool] = mapped_column(Boolean, default=True)
    alert_on_success: Mapped[bool] = mapped_column(Boolean, default=False)
    sla_time: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "HH:MM" 工作流时区
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
    parallel_degree: Mapped[int] = mapped_column(Integer, default=1)  # 补数批次并发度


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


class FeatureGroup(Base):
    """特征组:特征管理核心单元。绑定产出任务(workflow_id+task_key),生产即注册。
    schema(特征清单)变更升版本:同 (project,name) 下新行 version+1,旧版本并存。"""

    __tablename__ = "feature_groups"
    __table_args__ = (UniqueConstraint("project_id", "name", "version", name="uq_fg"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str] = mapped_column(Text, default="")
    entity_keys_json: Mapped[str] = mapped_column(Text, nullable=False)  # 主键列名 JSON 数组
    event_time_col: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ttl_days: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 在线 TTL
    online_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    offline_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # parquet/warehouse
    offline_location: Mapped[str] = mapped_column(String(255), nullable=False)  # 目录名或库表名
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    workflow_id: Mapped[int | None] = mapped_column(ForeignKey("workflows.id"), nullable=True)
    task_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_produced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_produced_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    materialize_watermark: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Feature(Base):
    __tablename__ = "features"
    __table_args__ = (UniqueConstraint("feature_group_id", "name", name="uq_feature"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feature_group_id: Mapped[int] = mapped_column(ForeignKey("feature_groups.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    dtype: Mapped[str] = mapped_column(String(32), default="double")
    description: Mapped[str] = mapped_column(Text, default="")  # 业务口径,审计留痕


class LineageEdge(Base):
    """血缘边:节点用 '类型:标识' 字符串(table:dw.x / feature_group:3 / workflow:5)。"""

    __tablename__ = "lineage_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    src: Mapped[str] = mapped_column(String(255), nullable=False)
    dst: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ApiKey(Base):
    """在线查询 API Key:仅存 sha256 哈希,明文只在创建时返回一次。"""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)  # 调用量统计(按请求次数)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Alert(Base):
    """站内告警。kind: run_failed/run_success/sla_miss/quality_drop/materialize_lag"""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    level: Mapped[str] = mapped_column(String(16), default="warning")  # info/warning/error
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="")
    workflow_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class QualityRecord(Base):
    """特征质量记录:每次成功产出落一条,供环比突变检测与趋势展示。"""

    __tablename__ = "quality_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feature_group_id: Mapped[int] = mapped_column(ForeignKey("feature_groups.id"), nullable=False)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    distinct_keys: Mapped[int | None] = mapped_column(Integer, nullable=True)
    null_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CustomDataset(Base):
    """自定义数据集(全局不分项目):key="{source}.{dataset}" 唯一,
    config_json 按 collector_type(http_json|tushare_api)解释,
    target_table=ods_{source}_{dataset} 创建时派生后不可变。"""

    __tablename__ = "custom_datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # snapshot/per_symbol
    collector_type: Mapped[str] = mapped_column(String(16), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    target_table: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SystemSetting(Base):
    """全局 KV 配置(webhook_url、quality_drop_ratio 等),管理员维护。"""

    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, default="")
