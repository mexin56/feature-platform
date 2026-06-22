# Phase 6: 因子挖掘与量化策略系统 设计文档

- 日期: 2026-06-22
- 状态: 已完成实施
- 关联计划: `docs/superpowers/plans/2026-06-22-phase6-factor-research.md`

## 1. 背景与目标

在特征调度平台基础上，构建基于沪深 300 成分股的量化因子研究系统——因子定义→计算→分析→回测→策略，集成到平台 Web UI。

## 2. 数据架构

```
storage/market.duckdb  (read-only, 行情+估值+财务)
storage/factors.db     (因子宽表, factor_values_latest)
storage/meta.db        (因子定义/策略/回测结果元数据)
```

- **因子存储**: DuckDB 宽表 `factor_values_latest` (trade_date, ts_code, factor_cols...)
- **行情拉取**: `scripts/backfill_hs300.py` 一次性补全脚本(pro_bar 逐股 + daily_basic 逐日)
- **成分股管理**: `backend/services/universe.py` — 时点版本化 + tushare 刷新

## 3. 核心模块

### 3.1 因子引擎 (`services/factor_engine.py`)
- `compute_factors()`: 读因子 SQL 定义 → 织入 mega-SQL CTE → DuckDB 执行 → 写宽表
- `normalize_factors()`: 截面 Z-score 归一化(可选)

### 3.2 因子分析 (`services/factor_analysis.py`)
- `analyze_factor()`: Pearson IC 序列 + 摘要 + 分位数收益 + IC 衰减
- `correlation_matrix()`: 因子间两两相关系数
- `combine_factors()`: 权重加权合成

### 3.3 回测引擎 (`services/backtest_engine.py`)
- 纯 DuckDB SQL CTE: 调仓日检测→选股→持仓→下一期收益→汇总绩效
- 绩效指标: 累计收益/年化/Sharpe/MaxDD/胜率/IR/换手率

### 3.4 API 路由 (`routers/factor_research.py`)
- 因子库 CRUD: GET/POST/PUT/DELETE `/api/factors`
- 成分股: GET/POST `/api/universe/hs300`
- 计算: POST `/api/factors/compute`
- 分析: GET `/api/factors/{id}/analysis`, POST `/api/factors/correlation-matrix`, POST `/api/factors/combine`
- 策略: CRUD `/api/strategies`
- 回测: POST `/api/strategies/{id}/backtest`, GET `/api/backtests/{id}`

## 4. 数据模型

| 模型 | 表名 | 核心字段 |
|------|------|----------|
| Factor | factors | name, category(price_volume/fundamental/industry/custom), formula_sql, direction |
| FactorComputation | factor_computations | factor_ids(JSON), start_date, end_date, status, rows |
| Strategy | strategies | factor_weights_json, top_n, rebalance_freq, weight_scheme, transaction_cost_bps |
| BacktestResult | backtest_results | strategy_id, metrics_json, returns_path |

## 5. 内置因子库 (22 个)

| 分类 | 子类 | 因子 | 方向 |
|------|------|------|------|
| 量价 | 动量 | ret_5d/ret_20d/ret_60d | 正向 |
| 量价 | 反转 | ret_5d_rev/ret_20d_rev | 正向 |
| 量价 | 波动率 | vol_20d/vol_60d | 反向 |
| 量价 | 换手率 | turnover_5d_avg/turnover_20d_avg/turnover_std_20d | 正向/反向 |
| 量价 | 振幅 | amplitude_20d | 正向 |
| 量价 | 流动性 | amount_20d_avg/vol_ratio_5d | 正向 |
| 量价 | 乖离率 | bias_20d | 正向 |
| 基本面 | 估值 | pe_ttm/pb_lf/ps_ttm | 反向 |
| 基本面 | 规模 | ln_cap/circulating_mv | 反向 |
| 基本面 | 盈利 | roe/dv_ratio | 正向 |
| 行业 | 估值 | industry_pb_rank | 正向 |

## 6. 前端页面

`pages/FactorResearch.jsx` — 四标签页:
- **因子库**: 分类筛选 + 因子表格 + 详情抽屉 + 新增因子 Modal
- **因子分析**: 单因子 IC 摘要卡片 + 衰减标签
- **因子组合**: 多选+权重滑块 + 相关性矩阵表 + 合成计算
- **策略回测**: 配置表单(选股数/调仓/权重/交易成本) → 绩效卡片(8 指标)

路由: `/factor-research`，菜单图标 FundOutlined，位于"数据查询"与"数据采集"之间。

## 7. 验收

1. `npm run build` 零错误
2. 后端启动后 `/api/factors` 返回 22 个内置因子
3. 浏览器 `http://localhost:5174/factor-research` 四标签页可操作
4. `scripts/backfill_hs300.py --dry-run` 无语法错误
5. 补齐数据后: 因子计算 → 分析 → 回测 端到端可走通

## 8. 已知限制 & 后续建议

### 8.1 当前限制
- **仅有 1 个交易日数据**（2026-06-12），因子 IC 为 0（无前置收益可计算），回测仅 1 日绩效。
- **成分股获取**: 无 tushare index_weight 数据时回退到全市场 5516 只股票，非真实 HS300。

### 8.2 补数据方法
```bash
# 拉取 2023-01-01 至今的 HS300 日线 + 估值数据
python scripts/backfill_hs300.py --start 2023-01-01

# 或仅测试少量股票:
echo -e "600519.SH\n000858.SZ\n601318.SH\n600036.SH\n000333.SZ" > /tmp/test_stocks.txt
python scripts/backfill_hs300.py --start 2024-01-01 --symbols-file /tmp/test_stocks.txt
```

### 8.3 后续增强方向
- **更多数据源**: 财务三表(利润/资产/现金)、一致预期、北向资金、融资融券
- **更多因子**: Alpha158 因子集、行业中性因子、Barra 风险因子
- **高级回测**: 多空组合、行业中性、动态调仓、滑点模型
- **可视化**: ECharts 累计收益曲线、回撤面积图、月度收益热力图（前端已有数据接口，缺图表组件）
- **因子看板**: 定期自动计算+缓存 IC，因子失效预警
