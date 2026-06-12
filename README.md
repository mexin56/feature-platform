# 特征调度管理平台(Feature Platform)

部门级特征调度管理系统:通过调度系统**生产**与管理风控离线特征和实时特征。参考 Airflow(时间语义/状态机)、DolphinScheduler(三层模型/补数)、Feast/Hopsworks(特征元数据/离线在线双存储)轻量自研,单机零中间件。

## 快速开始

```bash
# 环境:conda scpy310(Python 3.10),依赖见 requirements.txt
conda activate scpy310

# 一键启动(生产模式,前端已构建)
python run.py
# 浏览器访问 http://localhost:8100
# 默认管理员:admin / admin123(登录后请立即修改密码)
```

### 前端开发模式

```bash
cd frontend
npm install        # 首次
npm run dev        # 开发服务器 :5174,代理 /api → :8100
npm run build      # 构建产物 frontend/dist,由 :8100 静态托管
```

### 运行测试

```bash
python -m pytest tests/        # 180+ 用例
```

## 核心能力

- **调度内核**:5 秒 tick 循环(Cron 水位调度 → 依赖推进 → 孤儿清理 → SLA 检查 → 物化滞后检查),SQLite 状态机,crash-safe(重启不丢不重);工作流定义/实例/任务实例三层模型,定义版本快照;Airflow 式 `data_interval` 时间语义与模板变量(`{{ ds }}` 等);补数(串行/并行)、失败点重跑、强制置成功、超时强杀、心跳孤儿回收
- **任务插件**:`duckdb_sql`(本地计算产 Parquet + 质量三维)/ `sql_pushdown`(Spark ThriftServer/MySQL 下推)/ `python_script`(托管脚本)/ `materialize`(水位增量物化)/ `dependent`(跨工作流依赖)
- **特征管理**:特征组(版本化,schema 变更升版本并存)、特征口径留痕、血缘图、生产即注册(任务成功回写产出信息与质量记录)
- **在线特征服务**:独立 online_store.db,水位增量幂等物化;`POST /api/online-features`(API Key 认证、TTL 过期标记、批量 ≤500 键)
- **监控告警**:运行大盘、质量环比突变、SLA 超时、物化滞后;飞书 Webhook + 站内告警中心
- **多用户**:JWT + 三角色(管理员/开发者/只读)+ 项目隔离 + 操作审计

## 目录结构

```
backend/    FastAPI 后端(routers/ services/ services/plugins/)
frontend/   React 18 + Vite + AntD5 + ECharts + React Flow
storage/    meta.db / online_store.db / offline Parquet / 任务日志 / 托管脚本
docs/       设计文档(specs/)与实施计划(plans/)
tests/      pytest 全量用例
```

## 文档

- 设计文档:`docs/superpowers/specs/2026-06-12-feature-platform-design.md`
- 实施路线图:`docs/superpowers/plans/2026-06-12-roadmap.md`
