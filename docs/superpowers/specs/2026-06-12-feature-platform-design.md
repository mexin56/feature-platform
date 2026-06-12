# 特征调度管理平台(Feature Platform)设计文档

- 日期:2026-06-12
- 状态:已与用户逐节确认定稿
- 项目路径:`D:\feature-platform`
- 关联系统:`D:\ml-platform` 风控建模平台(文件/表级打通,见 §9)

## 1. 背景与定位

部门级特征调度管理系统:通过调度系统**生产**与管理风控离线特征和实时特征。参考 Airflow(时间语义/状态机)、DolphinScheduler(三层模型/补数/可视化 DAG)、Feast/Hopsworks(特征元数据/离线在线双存储)按单机轻量口径自研。

**场景边界**
- 单机部署(Windows 原生),浏览器访问 `http://localhost:8100`(开发期前端 :5174),局域网团队可访问,数据不出本机/数仓
- 零中间件:无 Redis/Celery/Docker/JVM,`python run.py` 一键拉起
- 多用户:账号 + 三角色 + 项目隔离(与 ml-platform 同模式,账号独立)
- 不做(预留扩展):消息流(Kafka/Flink)流式特征、分布式 Worker、LDAP/SSO

**核心使命**:特征不脱离调度孤立存在——每个特征组必须绑定产出任务,"生产即注册"。

## 2. 总体架构

```
浏览器(localhost / 局域网)
   │
React 18 SPA ── Vite + Ant Design 5 + ECharts + React Flow(可视化 DAG 编辑)
   │  HTTP/JSON(JWT)
FastAPI 后端(uvicorn :8100)
   ├─ 认证层:JWT + 三角色 + 项目成员校验
   ├─ API 层:特征组/工作流/实例/补数/在线特征/告警/系统管理
   ├─ 调度器线程:5s tick 循环(§5)
   ├─ 执行器:ProcessPoolExecutor 子进程池 + 任务插件(§6)
   ├─ 在线特征查询 API:/api/online-features(API Key 认证)
   └─ 存储
        ├─ SQLite 元数据库 meta.db(WAL):全部域模型与调度状态
        ├─ SQLite 在线特征库 online_store.db(WAL):仅最新特征值
        └─ 文件仓库 storage/
             ├─ offline/   本地 Parquet 特征快照(duckdb_sql 产出)
             ├─ logs/      任务实例日志(每实例一文件)
             └─ scripts/   托管 Python 脚本
```

**技术栈**:Python 3.10(conda scpy310)+ FastAPI + SQLAlchemy + pandas + duckdb + croniter + pyhive + pymysql + passlib + python-jose;前端 React 18 + Vite + Ant Design 5 + ECharts + React Flow。

**关键决策**
- 调度内核自研 tick 循环:所有调度决策由 SQLite 中状态推导(crash-safe),不依赖内存定时器;croniter 只做 Cron 解析
- SQLite 并发:WAL 模式,任务领取用 `UPDATE … WHERE state='queued'` 原子抢占,部门级并发量足够
- 模型三层分离 + 定义快照:改定义不影响在跑实例

## 3. 特征域模型

| 实体 | 字段要点 |
|---|---|
| 特征组 FeatureGroup | 名称、版本(schema 变更升版本,v1/v2 并存)、主键列(entity keys)、事件时间列、TTL、是否启用在线、负责人、归属项目、离线落地(`warehouse_table` 数仓库表名 或 `parquet` 本地路径)、最近产出时间/行数 |
| 特征 Feature | 从属特征组:名称、类型、业务口径说明(留痕供审计) |
| 血缘 Lineage | 边表:`源表 → 特征组 → 下游(在线物化/ml-platform 数据集)`;特征组与产出它的工作流任务双向关联 |

**生产即注册**:特征组创建时即绑定产出任务节点;调度成功一次即回写"最近产出时间/行数"。

## 4. 调度域模型

### 4.1 三层模型(DolphinScheduler 式)

- **工作流定义 Workflow**(版本化模板):名称、项目、DAG(节点+边 JSON)、Cron 表达式、时区、catchup 开关、失败策略(继续/结束)、并发上限、告警策略、上线/下线状态。每次修改产生新版本;实例持有定义快照。
- **工作流实例 WorkflowRun**:定义版本快照、`data_interval_start/end`、`run_type`(scheduled/manual/backfill)、状态(running/success/failed/stopped)、触发人、起止时间。
- **任务实例 TaskInstance**:run_id、节点 key、状态机、try_number、心跳时间、日志文件路径、起止时间。

### 4.2 时间语义(Airflow 式)

每个实例绑定 `data_interval`,"区间结束后处理该区间的数据"。SQL/脚本中模板变量按实例区间渲染:`{{ ds }}`、`{{ data_interval_start }}`、`{{ data_interval_end }}` 等——同一条 SQL 既日常跑也补数。

### 4.3 任务状态机

```
none → scheduled → queued → running → success
                                   ↘ failed(重试次数耗尽)
                                   ↘ up_for_retry →(间隔后)→ queued
上游失败 → upstream_failed;条件跳过 → skipped
```

全部状态落库;状态流转只发生在调度器线程与执行器回写两处。

### 4.4 节点配置与依赖

- 节点:类型 + 参数 JSON + 重试次数/间隔 + 超时(execution_timeout,超时 kill 子进程)+ 上游依赖列表
- 跨工作流依赖 `dependent` 节点:检查"工作流 X 在对应周期是否成功",轮询等待,可设等待超时
- 失败策略二元:继续(其余并行分支跑完)/ 结束(终止全部)

### 4.5 补数 backfill 与手工操作

- 补数:指定日期区间批量生成实例;串行 / 并行(可设并发度)两种模式
- 手工:单实例重跑、失败任务从失败点续跑、强制置成功、终止实例

## 5. 调度内核(tick 循环)

调度器为后端进程内单线程,每 5s 一轮:

1. **Cron 扫描**:对每个上线工作流,依据落库的 `last_scheduled_at` 水位用 croniter 推算 `next_run ≤ now` 的全部区间 → 创建实例(catchup=False 时只取最新一个区间);重启后从水位补算,不丢调度
2. **依赖推进**:扫 running 实例,把"上游全 success"的任务置 queued;执行器空闲槽位领取(原子抢占),受全局与每工作流并发上限约束
3. **孤儿清理**:running 但心跳超时(>60s)的任务实例 → 按剩余重试次数置 up_for_retry 或 failed(Airflow 同款孤儿清理);启动时执行一次全量清理

执行器:ProcessPoolExecutor;子进程执行插件逻辑,定期写心跳,stdout/stderr 重定向至日志文件;超时由父进程监控强杀。

## 6. 任务插件(type + params 插件表)

| 类型 | 行为 |
|---|---|
| `sql_pushdown` | SQL 渲染模板变量后发往 Spark ThriftServer / MySQL 源端执行(典型:INSERT OVERWRITE 数仓特征表);执行后可选行数校验,产出 0 行触发告警 |
| `duckdb_sql` | 从源拉数在本地 DuckDB 计算,产出 Parquet 特征快照(中小数据量/文件类来源) |
| `python_script` | 平台托管脚本子进程执行,注入区间环境变量,stdout/stderr 全量进日志 |
| `materialize` | 在线物化:从离线特征表按水位增量读取 → upsert 在线存储(§7) |
| `dependent` | 跨工作流依赖检查(§4.4) |

**连接管理**:管理员统一配置 MySQL / Spark ThriftServer 连接(密码 Fernet 加密存储、测试连通),任务经连接 ID 引用——与 ml-platform 同口径。

## 7. 在线特征服务

- **在线存储**:`online_store.db`,表 `(feature_group_id, entity_key) → 特征值 JSON + updated_at`,主键索引,毫秒级点查
- **物化水位**:每特征组记录"已物化到的 event_time";`materialize` 增量推进,幂等 upsert,失败重跑不重不漏
- **查询 API**:`POST /api/online-features`,入参特征组 + 主键列表,返回最新特征值;超 TTL 返回 `null + expired 标记`;**API Key 认证**(管理员签发/吊销),记录调用量
- **调试台**:前端手工输入主键即时查询

## 8. 监控、告警与权限

- **运行监控**:首页大盘(今日成功/失败/运行中、耗时 Top、失败列表)、实例甘特图 + DAG 着色视图、日志在线查看
- **特征质量(轻量)**:每次产出记录行数/主键去重数/空值率,环比突变告警(默认行数环比降幅 >50% 触发,可配);物化滞后(水位落后超阈值)告警
- **告警**:Webhook(预置飞书机器人卡片格式)+ 站内告警中心;按"失败/成功/SLA 超时"绑定工作流;SLA="应在 HH:MM 前完成"
- **权限**:本地账号(bcrypt)+ JWT;三角色:管理员(用户/连接/API Key/所有项目)、开发者(建项目、项目内全部操作)、只读;项目隔离成员制;关键动作留痕(上线/下线、补数、重跑、置成功、API Key 签发)

## 9. 与 ml-platform 的打通

- 连接配置口径一致(MySQL / Spark ThriftServer),两边各自配置
- 本系统产出特征表(数仓表 / Parquet)→ ml-platform 用已有 SQL 取数 / 文件导入能力消费
- **文件/表级打通,不做 API 级耦合**,两系统独立演进

## 10. 前端页面

① 登录 ② 项目工作台(大盘)③ 特征组管理(清单/版本/血缘图/在线状态)④ 工作流编辑器(React Flow 拖拽 DAG + 节点配置面板 + SQL 编辑器带模板变量提示)⑤ 调度与补数管理 ⑥ 实例监控(列表/DAG 视图/日志/重跑)⑦ 在线特征(水位/调试台)⑧ 告警中心 ⑨ 系统管理(用户/连接/API Key)

## 11. 测试策略

pytest 全覆盖:
- 内核单测:Cron 水位补算(含重启场景)、依赖解析、状态机流转、孤儿清理、补数串/并行语义、模板变量渲染
- 插件:DuckDB 真跑;SQL 下推 mock 连接;materialize 水位幂等
- API:认证/权限/项目隔离/CRUD/在线查询(TTL 过期)
- 端到端冒烟:建特征组 → 编排工作流 → 触发 → 物化 → 在线查询全链路

## 12. 错误处理要点

- 任务级:重试(次数+间隔)、超时强杀、失败告警;0 行产出告警
- 实例级:失败策略(继续/结束)、失败点续跑
- 系统级:进程崩溃重启后水位补算 + 孤儿清理,调度不丢不重;SQLite 写冲突由 WAL + 原子抢占规避
- 在线查询:TTL 过期显式标记,不静默返回陈旧值
