# Phase 4:前端 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. 前端以「构建通过 + API 对接正确 + 浏览器手工验收」为准,不写前端单测(沿用 ml-platform Phase 1C 验收模式,设计文档 §10)。

**Goal:** React SPA 覆盖 9 页面,`npm run build` 产物由 FastAPI(:8100)静态托管,浏览器全流程可用——平台验收里程碑。

**Tech:** React 18 + Vite 5 + Ant Design 5(中文 zh_CN)+ ECharts 5 + react-router-dom 6。开发期 Vite **:5174** 代理 `/api`;生产单端口 :8100。**风格与代码组织参照 `D:\ml-platform\frontend`**(api.js 的 fetch 封装、App.jsx 布局、pages/ 一页一文件)。

**DAG 编辑器选型:** 首选 `@xyflow/react`(react-flow);若 npm 安装失败则降级方案:表单式编辑(节点表格 + 边多选)+ ECharts graph 只读预览。两种都满足验收。

**约定:** 命令在 `D:\feature-platform`;npm 直接用系统 npm;Python `D:/conda/envs/scpy310/python.exe`。分支 `feature/phase4-frontend`。每任务结束:`cd frontend && npm run build` 必须成功,`git status` 干净后提交。

---

## 页面与路由

| 路由 | 页面文件 | 功能要点(对接 API) |
|---|---|---|
| /login | Login.jsx | 登录(POST /api/auth/login),存 token;登录后选/建项目 |
| / | Dashboard.jsx | 大盘:今日成功/失败/运行中卡片、最近失败表、特征组滞后表(GET /api/monitoring/dashboard);未读告警角标 |
| /feature-groups | FeatureGroups.jsx | 列表(版本/在线/最近产出/水位),新建抽屉(含特征清单编辑、上游表、绑定工作流节点) |
| /feature-groups/:id | FeatureGroupDetail.jsx | 详情:特征清单、口径、血缘图(ECharts graph:上游表→特征组→工作流)、质量趋势(QualityRecord 暂以 last_produced 展示)、在线调试台(POST /api/feature-groups/{id}/online-debug) |
| /workflows | Workflows.jsx | 列表(cron/状态/版本),上线/下线、触发、补数对话框(区间+并发度) |
| /workflows/:id | WorkflowEditor.jsx | DAG 编辑(react-flow 或降级方案)+ 节点配置抽屉(五类插件参数表单,SQL 用 TextArea)+ 元信息(cron/时区/catchup/并发/失败策略/告警开关/SLA)+ 保存(PUT,提示升版本) |
| /runs | Runs.jsx | 实例列表(跨工作流,筛选状态/类型;轮询 5s)+ 操作(终止/重跑) |
| /runs/:id | RunDetail.jsx | 任务实例表(状态着色/try/耗时)+ 日志查看(GET /api/tasks/{tid}/log,Modal 内 pre)+ 置成功/重跑/终止 |
| /alerts | Alerts.jsx | 告警中心(级别/类型筛选、已读标记) |
| /admin | Admin.jsx | 管理员:用户管理、连接管理(含测试连通)、API Key(创建后明文一次展示)、Webhook 设置(PUT /api/settings/webhook_url) |

公共:App.jsx 侧边导航(工作台/特征组/工作流/实例/告警/管理),顶部项目切换器(X-Project-Id 注入 api.js)+ 用户菜单(改密/退出);未登录跳 /login;viewer 隐藏写操作按钮(以 401/403 兜底)。

## 文件清单

```
frontend/
  package.json  vite.config.js  index.html
  src/
    main.jsx  App.jsx  api.js  styles.css
    pages/ Login.jsx Dashboard.jsx FeatureGroups.jsx FeatureGroupDetail.jsx
           Workflows.jsx WorkflowEditor.jsx Runs.jsx RunDetail.jsx
           Alerts.jsx Admin.jsx
    components/ Chart.jsx(ECharts 封装,参照 ml-platform)StateTag.jsx(状态→颜色)
```

## 任务拆分

### Task 1: 骨架 + 登录 + 布局 + 静态托管
- frontend 脚手架(package.json:react/react-dom/react-router-dom/antd/echarts/@xyflow/react;vite.config:5174、proxy /api→http://localhost:8100)
- api.js:fetch 封装(JWT Bearer、X-Project-Id、401 跳登录、错误 message 弹出)
- App.jsx 布局 + 路由(各页先放占位)+ Login.jsx + 项目切换器(GET/POST /api/projects)
- backend/app.py 末尾挂载 dist(照抄 ml-platform `_mount_frontend` 模式:/assets 静态 + catch-all 返回 index.html,顺序在所有 /api 之后;dist 不存在时跳过)
- 验收:npm install && npm run build 成功;pytest 184 全绿;TestClient GET / 返回 index.html(新增 1 个后端测试 tests/test_frontend_mount.py:构建产物存在时验证 / 与 /assets,不存在时跳过 skipif)
- Commit: `feat: 前端骨架/登录/布局/静态托管`

### Task 2: Dashboard + Alerts + Admin
- 三页面按上表实现;Dashboard 卡片用 AntD Statistic,滞后表 lag_hours>阈值标红
- Admin 四个 Tab(用户/连接/API Key/Webhook);API Key 创建成功 Modal 展示明文并提示仅此一次
- 验收:npm run build;手工冒烟说明写入报告
- Commit: `feat: 工作台大盘/告警中心/系统管理页面`

### Task 3: 特征组两页面
- 列表 + 新建抽屉(特征清单用可编辑表格 AntD Table + Form.List;绑定工作流节点:级联选择工作流→节点 key)
- 详情:血缘 ECharts graph(节点分三类着色);在线调试台(主键输入→查询结果 JSON 展示,expired 标红)
- 验收:npm run build
- Commit: `feat: 特征组列表/详情/血缘/在线调试台`

### Task 4: 工作流两页面(含 DAG 编辑器)
- 列表页:上线/下线 Switch、触发按钮、补数 Modal(RangePicker+并发度 InputNumber)
- 编辑器:react-flow 画布(节点增删连线)+ 点击节点开配置抽屉(type Select 切换参数表单:duckdb_sql{sql,output_name,entity_keys}/sql_pushdown{connection_id 下拉,sql 或 sqls,count_sql,expect_rows_min}/python_script{script}/materialize{feature_group_id 下拉}/dependent{workflow_id 下拉};retries/retry_delay/timeout 通用)+ 元信息表单;保存→PUT(若 DAG 变化后端自动升版本,提示新版本号)
- 若 @xyflow/react 不可用:降级方案(写明在代码注释)
- 验收:npm run build
- Commit: `feat: 工作流列表/DAG 编辑器`

### Task 5: 实例监控两页面
- Runs 列表:5s 轮询(页面可见时),state Tag 着色,run_type 标签,操作列
- RunDetail:任务表(state/try_number/max_tries/started/finished/耗时)、日志 Modal、操作按钮按状态禁用
- 验收:npm run build
- Commit: `feat: 实例监控/任务日志`

### Task 6: 集成验收与收尾
- `cd frontend && npm run build`;`D:/conda/envs/scpy310/python.exe -m pytest tests/`(185 全绿:184+frontend_mount)
- 真实端到端冒烟:python -c 启动 TestClient(sync_scheduler=False)确认 / 可访问;报告输出"手工验收清单"(登录→建项目→建连接→建工作流→建特征组→触发→看实例→调试台查询→告警中心)
- README.md 更新:启动方式(python run.py;开发期 cd frontend && npm run dev)
- Commit: `feat: 前端集成收尾与 README`

## 验收标准(整阶段)

1. `npm run build` 零错误;2. pytest 全绿不回归;3. `python run.py` 单端口 :8100 全功能可用(SPA 路由刷新不 404);4. 9 页面与上表功能点一致;5. 手工验收清单交付用户。
