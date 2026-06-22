# Phase 6: 因子挖掘与量化策略系统 实施计划

## T1: HS300 成分股 + 历史数据补全
- `backend/services/collectors/tushare_src.py`: +index_weight 采集器
- `backend/services/universe.py`: get_constituents / refresh (时点版本化)
- `scripts/backfill_hs300.py`: 独立补数据脚本(pro_bar 逐股+聚合写入)
- Commit: `feat: HS300成分股权重采集+历史行情补脚本`

## T2: 因子模型 + 内置因子库种子
- `backend/models.py`: +Factor / FactorComputation / Strategy / BacktestResult
- `backend/services/factor_seed.py`: 22 个预定义因子
- `backend/app.py`: +_seed_factors() 启动播种 + 建表迁移
- Commit: `feat: 因子模型+22内置因子库启动种子`

## T3: 因子计算引擎
- `backend/services/factor_engine.py`: compute_factors (mega-SQL CTE) + normalize_factors
- Commit: `feat: 因子计算引擎(DuckDB mega-SQL)`

## T4: 因子 CRUD + 计算 API
- `backend/routers/factor_research.py`: factor CRUD/compute/universe 端点
- `backend/app.py`: 注册 factor_research 路由
- Commit: `feat: 因子库CRUD+计算+成分股API端点`

## T5: 因子分析引擎
- `backend/services/factor_analysis.py`: IC/分位数/衰减/相关性/VIF
- Commit: `feat: 因子分析引擎(IC/分位数/衰减/相关性)`

## T6: 分析 + 策略 + 回测 API
- `backend/routers/factor_research.py`: 扩展 analysis/strategy/backtest 端点
- Commit: `feat: 因子分析+策略回测API端点`

## T7: 回测引擎
- `backend/services/backtest_engine.py`: 纯 SQL CTE 回测 + 绩效
- Commit: `feat: DuckDB SQL回测引擎(月度调仓/TopN选股)`

## T8: 前端因子研究页
- `frontend/src/pages/FactorResearch.jsx`: 四标签页(因子库/分析/组合/回测)
- `frontend/src/App.jsx`: +菜单项 + 路由
- Commit: `feat: 因子研究前端页面(四标签页)`

## T9: 设计文档
- `docs/superpowers/specs/2026-06-22-factor-research-design.md`
- `docs/superpowers/plans/2026-06-22-phase6-factor-research.md`
- Commit: `docs: Phase6因子研究设计文档`
