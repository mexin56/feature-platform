"""内置因子库种子:首次启动时插入 20+ 常用量化因子定义。
每个因子含 DuckDB SQL 公式模板,引擎运行时拼入标准外覆 CTE 计算出宽表。
注意: DuckDB 不支持 STDDEV_SAMP(x, N) 缩写,必须写 STDDEV_SAMP(x) OVER (... ROWS N PRECEDING)。
"""

PREDEFINED_FACTORS = [
    # ═══ 量价 — 动量 ═══
    dict(name="ret_5d", name_cn="5日动量", category="price_volume",
         subcategory="动量", direction=1, required_tables="ods_tushare_daily",
         formula_sql="(close / LAG(close,5) OVER (PARTITION BY ts_code ORDER BY trade_date) - 1)",
         description="过去 5 个交易日累计收益率,正号=做多方向"),
    dict(name="ret_20d", name_cn="20日动量", category="price_volume",
         subcategory="动量", direction=1, required_tables="ods_tushare_daily",
         formula_sql="(close / LAG(close,20) OVER (PARTITION BY ts_code ORDER BY trade_date) - 1)",
         description="过去 20 个交易日累计收益率,反映中期趋势强度"),
    dict(name="ret_60d", name_cn="60日动量", category="price_volume",
         subcategory="动量", direction=1, required_tables="ods_tushare_daily",
         formula_sql="(close / LAG(close,60) OVER (PARTITION BY ts_code ORDER BY trade_date) - 1)",
         description="过去 60 个交易日累计收益率,反映长期趋势"),

    # ═══ 量价 — 反转 ═══
    dict(name="ret_5d_rev", name_cn="5日反转", category="price_volume",
         subcategory="反转", direction=1, required_tables="ods_tushare_daily",
         formula_sql="-(close / LAG(close,5) OVER (PARTITION BY ts_code ORDER BY trade_date) - 1)",
         description="5日收益取负,捕捉短期均值回复(反转效应)"),
    dict(name="ret_20d_rev", name_cn="20日反转", category="price_volume",
         subcategory="反转", direction=1, required_tables="ods_tushare_daily",
         formula_sql="-(close / LAG(close,20) OVER (PARTITION BY ts_code ORDER BY trade_date) - 1)",
         description="20日收益取负,捕捉中期反转"),

    # ═══ 量价 — 波动率 ═══
    dict(name="vol_20d", name_cn="20日波动率", category="price_volume",
         subcategory="波动率", direction=-1, required_tables="ods_tushare_daily",
         formula_sql="STDDEV_SAMP(pct_chg/100.0) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)",
         description="20日收益率标准差(pct_chg 列),高波动率通常伴随高风险溢价"),
    dict(name="vol_60d", name_cn="60日波动率", category="price_volume",
         subcategory="波动率", direction=-1, required_tables="ods_tushare_daily",
         formula_sql="STDDEV_SAMP(pct_chg/100.0) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)",
         description="60日收益率标准差(pct_chg 列)"),

    # ═══ 量价 — 换手率 ═══
    dict(name="turnover_5d_avg", name_cn="5日均换手率", category="price_volume",
         subcategory="换手率", direction=1, required_tables="ods_tushare_daily_basic",
         formula_sql="AVG(turnover_rate) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)",
         description="过去 5 日换手率均值,高换手代表市场关注度高"),
    dict(name="turnover_20d_avg", name_cn="20日均换手率", category="price_volume",
         subcategory="换手率", direction=1, required_tables="ods_tushare_daily_basic",
         formula_sql="AVG(turnover_rate) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)",
         description="过去 20 日换手率均值"),
    dict(name="turnover_std_20d", name_cn="20日换手率标准差", category="price_volume",
         subcategory="换手率", direction=-1, required_tables="ods_tushare_daily_basic",
         formula_sql="STDDEV_SAMP(turnover_rate) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)",
         description="20日换手率标准差,反映交易热度波动"),

    # ═══ 量价 — 振幅 ═══
    dict(name="amplitude_20d", name_cn="20日均振幅", category="price_volume",
         subcategory="振幅", direction=1, required_tables="ods_tushare_daily",
         formula_sql="AVG((high-low)/NULLIF(pre_close,0)) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)",
         description="过去 20 日振幅均值,高振幅代表价格波动区间大"),

    # ═══ 量价 — 流动性 ═══
    dict(name="amount_20d_avg", name_cn="20日均成交额", category="price_volume",
         subcategory="流动性", direction=1, required_tables="ods_tushare_daily",
         formula_sql="AVG(amount) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)",
         description="过去 20 日日均成交额,衡量流动性"),
    dict(name="vol_ratio_5d", name_cn="5日均量比", category="price_volume",
         subcategory="流动性", direction=1, required_tables="ods_tushare_daily_basic",
         formula_sql="AVG(volume_ratio) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)",
         description="过去 5 日量比均值,大于 1 为放量"),

    # ═══ 量价 — 乖离率 ═══
    dict(name="bias_20d", name_cn="20日乖离率", category="price_volume",
         subcategory="乖离率", direction=1, required_tables="ods_tushare_daily",
         formula_sql="(close / AVG(close) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) - 1)",
         description="收盘价与 20 日均线的偏离程度"),

    # ═══ 基本面 — 估值 ═══
    dict(name="pe_ttm", name_cn="市盈率 TTM", category="fundamental",
         subcategory="估值", direction=-1, required_tables="ods_tushare_daily_basic",
         formula_sql="pe_ttm", description="滚动市盈率,低估值通常伴随未来正收益"),
    dict(name="pb_lf", name_cn="市净率 LF", category="fundamental",
         subcategory="估值", direction=-1, required_tables="ods_tushare_daily_basic",
         formula_sql="pb", description="最新财报市净率,低 PB 价值策略"),
    dict(name="ps_ttm", name_cn="市销率 TTM", category="fundamental",
         subcategory="估值", direction=-1, required_tables="ods_tushare_daily_basic",
         formula_sql="ps_ttm", description="滚动市销率"),

    # ═══ 基本面 — 规模 ═══
    dict(name="ln_cap", name_cn="对数市值", category="fundamental",
         subcategory="规模", direction=-1, required_tables="ods_tushare_daily_basic",
         formula_sql="LN(NULLIF(total_mv,0))", description="总市值自然对数,捕捉小盘股溢价"),
    dict(name="circulating_mv", name_cn="流通市值", category="fundamental",
         subcategory="规模", direction=-1, required_tables="ods_tushare_daily_basic",
         formula_sql="circ_mv", description="流通市值"),

    # ═══ 基本面 — 盈利质量 ═══
    dict(name="roe_derived", name_cn="ROE(推导)", category="fundamental",
         subcategory="盈利质量", direction=1, required_tables="ods_tushare_daily_basic",
         formula_sql="dv_ratio/100.0 * pe_ttm / NULLIF(pb,0)",
         description="从 PB/PE/股息率反向推导 ROE"),
    dict(name="dv_ratio", name_cn="股息率", category="fundamental",
         subcategory="盈利质量", direction=1, required_tables="ods_tushare_daily_basic",
         formula_sql="dv_ratio", description="股息率(原始数值),高股息为防御策略方向"),

    # ═══ 行业 ═══
    dict(name="industry_pb_rank", name_cn="PB分位数", category="industry",
         subcategory="行业", direction=1, required_tables="ods_tushare_daily_basic",
         formula_sql="PERCENT_RANK() OVER (PARTITION BY trade_date ORDER BY pb)",
         description="全市场 PB 排名分位值,跨行业可比"),
]


def seed_factors(SessionLocal) -> int:
    """首次启动:将内置因子写入 factors 表(仅当表为空时)。"""
    from sqlalchemy import func, select
    from ..models import Factor

    with SessionLocal() as db:
        cnt = db.scalar(select(func.count(Factor.id))) or 0
        if cnt > 0:
            return 0
        for f in PREDEFINED_FACTORS:
            db.add(Factor(is_builtin=True, **f))
        db.commit()
        return len(PREDEFINED_FACTORS)
