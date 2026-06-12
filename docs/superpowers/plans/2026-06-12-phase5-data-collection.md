# Phase 5: 数据采集(行情数据湖)

## 目标
收录开源金融数据源目录(10 源),实现可运行采集器(8 源),通过现有调度系统每日采集**全市场**数据,统一落 `storage/market.duckdb`,供数据查询页与 duckdb_sql 特征衍生直接使用。

## 口径(已与用户确认)
- 源范围:**能跑的全部实现**——腾讯/新浪/东方财富/同花顺/巨潮(纯 HTTP,httpx)、akshare/baostock/mootdx(pip 包,import 守卫降级)、tushare(**用户已提供 token 与专用网关,初始化必须且只能经由 `backend/services/collectors/tushare_client.py` 的 get_pro()/pro_bar(),该文件已固化勿改调用方式**)、QMT(仅入目录,标注"需本机 QMT 终端",不实现)。
- 个股级数据集(逐股调用):**可配股票池**(params.symbols 列表),快照类(一次调用返全市场)无需股票池;逐股循环带限频 sleep(默认 0.5s/次,可配 args.interval_sec)。
- 存储:`storage/market.duckdb`,表名 `ods_{source}_{dataset}`,统一附加列 `dt VARCHAR`(数据日期 YYYY-MM-DD)与 `collected_at VARCHAR`;幂等写入 = `DELETE WHERE dt=?` 后 INSERT(首跑 CREATE TABLE AS)。
- 衍生打通:query 页 duckdb 引擎与 duckdb_sql 插件均 `ATTACH market.duckdb AS market (READ_ONLY)`(文件存在时),表以 `market.ods_xxx` 查询;query 目录端点返回 market 表清单+字段。
- 调度:复用现有工作流。`POST /api/datasets/seed-workflow` 按所选数据集生成工作流(data_collect 任务**线性串链**防限频,cron 默认 `0 17 * * 1-5`)。

## 架构
```
backend/services/collectors/
  __init__.py     # CATALOG: dict[key]->DataSet; register(); availability()
  base.py         # DataSet dataclass: key(source.dataset), source, name, module, desc,
                  #   mode(snapshot|per_symbol), requires(None|token|package|terminal),
                  #   target_table, fetch(args, ctx) -> (columns, rows)
  writer.py       # write_market(settings, table, dt, columns, rows) -> rowcount
  tencent.py sina.py eastmoney.py ths.py cninfo.py        # HTTP(httpx, timeout=15, UA)
  akshare_src.py baostock_src.py mootdx_src.py tushare_src.py  # import 守卫
  qmt_src.py      # 仅目录条目, available=False
backend/services/plugins/data_collect.py
  # params: {dataset_key, args?: {symbols?, interval_sec?, ...}}
  # ctx 注入 settings/dt(data_interval_end 的日期);写 market.duckdb;result={table, rows, dt}
backend/routers/datasets.py
  # GET  /api/datasets            -> 目录+可用性+统计(market.duckdb: 表行数/max(dt))
  # POST /api/datasets/seed-workflow {name, cron, dataset_keys[], symbols?} -> 建工作流
  # GET/PUT tushare token 复用 settings 路由(key=tushare_token)
frontend/src/pages/DataCollect.jsx  # 导航「数据采集」,位于 数据查询 之后
```

## 目录编目要求(尽可能全,源自 quant-feature-derive.html)
- tushare:daily/weekly/monthly/daily_basic/stk_limit/suspend_d/moneyflow/moneyflow_hsgt/
  fina_indicator/income/balancesheet/cashflow/forecast/limit_list/top_list/top_inst/
  adj_factor/hs_const(均 requires=token;trade_date 全市场快照优先)
- akshare:stock_zh_a_spot_em(快照)/stock_zh_a_hist(逐股)/board_industry_name_em/
  board_concept_name_em/board_industry_hist_em(逐板块)/individual_fund_flow(逐股)/
  market_fund_flow/sector_fund_flow_rank/zt_pool_em/zt_pool_strong_em/zt_pool_dtgc_em/
  zt_pool_zbgc_em/stock_hot_rank_em/market_activity_em/margin_sz_sh_daily/
  hsgt_north_net_flow_in_em 等
- baostock:query_history_k_data_plus(日/周/月,逐股)/profit/operation/growth/balance/
  cash_flow/dupont(逐股按季)
- 腾讯:实时行情批量快照(全市场分批)、六大指数快照
- 东方财富:行业/概念板块列表、全市场资金流快照、龙虎榜当日、融资融券、大宗交易、
  解禁日历、全球快讯、个股新闻(逐股)
- 同花顺:热点题材、北向资金分时、一致预期(逐股)、日内资金流(逐股)
- 新浪:利润表/资产负债表/现金流量表(逐股按季)
- 巨潮:公告检索(逐股)
- mootdx:实时五档快照(逐股分批)、财务快照(逐股)
- QMT:目录条目全部收录,available=False,备注"需本机 QMT 终端"

## 任务
- T1 框架+插件:collectors base/registry/writer + data_collect 插件 + query/duckdb_sql ATTACH
  + tencent 采集器(参考实现) + 测试(mock fetch,真实写 duckdb 验幂等)。
  Commit: `feat: 数据采集框架/data_collect 插件/market.duckdb 打通`
- T2 全量采集器:sina/eastmoney/ths/cninfo + akshare/baostock/mootdx(import 守卫)
  + tushare(token) + qmt(目录) + 每源轻量测试(mock HTTP/库)。
  Commit: `feat: 八源采集器与数据集目录`
- T3 API:datasets 路由(目录/统计/seed-workflow)+ 测试。
  Commit: `feat: 数据集目录 API 与一键采集工作流`
- T4 前端:数据采集页(按源分组目录、可用性徽标、统计列、勾选生成工作流 Modal、
  tushare token 配置入口)+ 导航路由 + build。
  Commit: `feat: 数据采集页`
- T5 终审:契约清查+全量回归+真实网络冒烟(腾讯快照采一次)→ 合并 main → 重启服务。

## 验收
pytest 全绿;真实运行一次采集工作流后,查询页 duckdb 引擎能 `select * from market.ods_tencent_spot` 出全市场数据;CSV 可导出。
