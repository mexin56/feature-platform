"""tushare 采集器:初始化仅经由 tushare_client.get_pro()/pro_bar()(专用网关,勿改)。
快照类一次调用全市场(trade_date=dt 去横杠);财报类逐股循环(ts_code + limit);
pro_bar_daily 逐股前复权日线(默认 dt 前 30 天窗口)。"""
from . import _common as c
from . import register
from .base import DataSet
from .tushare_client import get_pro, pro_bar


def _pro(ctx: dict):
    """pro 客户端:ctx.tushare_token(SystemSetting 经 data_collect 插件注入)
    > 环境变量 FP_TUSHARE_TOKEN > tushare_client 内置默认。"""
    import os

    token = (ctx or {}).get("tushare_token") or os.environ.get("FP_TUSHARE_TOKEN")
    return get_pro(token or None)


def _reg(dataset: str, name: str, desc: str, mode: str, fetch) -> None:
    register(DataSet(
        key=f"tushare.{dataset}", source="tushare", name=name, module="tushare",
        desc=desc, mode=mode, requires="token",
        target_table=f"ods_tushare_{dataset}", fetch=fetch))


def _snapshot_fetch(api_name: str):
    def fetch(args, ctx):
        pro = _pro(ctx)
        df = getattr(pro, api_name)(trade_date=c.nodash(c.ctx_dt(ctx)))
        return c.df_to_table(df)
    return fetch


def fetch_limit_list(args, ctx):
    """涨跌停榜:新接口 limit_list_d 优先,旧版 limit_list 兜底(版本兼容)。"""
    pro = _pro(ctx)
    nd = c.nodash(c.ctx_dt(ctx))
    err = None
    for api in ("limit_list_d", "limit_list"):
        try:
            return c.df_to_table(getattr(pro, api)(trade_date=nd))
        except Exception as e:  # noqa: BLE001
            err = e
    raise RuntimeError(f"tushare 涨跌停榜接口调用失败: {err}")


def fetch_hs_const(args, ctx):
    """沪深股通成份:hs_type=SH/SZ 各一次调用后拼接。"""
    import pandas as pd

    pro = _pro(ctx)
    frames = [pro.hs_const(hs_type=t) for t in ("SH", "SZ")]
    return c.df_to_table(pd.concat(frames, ignore_index=True))


def _per_symbol_fetch(api_name: str):
    def fetch(args, ctx):
        pro = _pro(ctx)
        limit = int((args or {}).get("limit", 8))
        return c.per_symbol_df(
            args, lambda sym: getattr(pro, api_name)(ts_code=sym, limit=limit))
    return fetch


def fetch_pro_bar_daily(args, ctx):
    """逐股前复权日线:必须经 tushare_client.pro_bar(api=pro)。"""
    pro = _pro(ctx)
    a = args or {}
    dt = c.ctx_dt(ctx)
    start = c.nodash(a.get("start_date") or c.days_before(dt, 30))
    end = c.nodash(a.get("end_date") or dt)
    return c.per_symbol_df(args, lambda sym: pro_bar(
        pro, ts_code=sym, adj="qfq", start_date=start, end_date=end))


_SNAPSHOTS = [
    ("daily", "日线行情", "pro.daily(trade_date) 全市场日线"),
    ("weekly", "周线行情", "pro.weekly(trade_date) 全市场周线(周最后交易日)"),
    ("monthly", "月线行情", "pro.monthly(trade_date) 全市场月线(月最后交易日)"),
    ("daily_basic", "每日指标", "pro.daily_basic 全市场估值/换手/市值等每日指标"),
    ("stk_limit", "涨跌停价格", "pro.stk_limit 全市场当日涨跌停价"),
    ("suspend_d", "停复牌信息", "pro.suspend_d 当日停复牌"),
    ("moneyflow", "个股资金流向", "pro.moneyflow 全市场个股大单资金流"),
    ("moneyflow_hsgt", "沪深港通资金流向", "pro.moneyflow_hsgt 北向/南向资金"),
    ("top_list", "龙虎榜每日明细", "pro.top_list 当日龙虎榜上榜个股"),
    ("top_inst", "龙虎榜机构明细", "pro.top_inst 当日龙虎榜机构席位"),
    ("adj_factor", "复权因子", "pro.adj_factor 全市场复权因子"),
]
for _d, _n, _desc in _SNAPSHOTS:
    _reg(_d, _n, _desc, "snapshot", _snapshot_fetch(_d))

_reg("limit_list", "涨跌停统计", "limit_list_d/limit_list(版本兼容)当日涨跌停",
     "snapshot", fetch_limit_list)
_reg("hs_const", "沪深股通成份股", "pro.hs_const SH+SZ 两次调用拼接",
     "snapshot", fetch_hs_const)

_FIN = [
    ("fina_indicator", "财务指标", "pro.fina_indicator 逐股近 N 期财务指标"),
    ("income", "利润表", "pro.income 逐股近 N 期利润表"),
    ("balancesheet", "资产负债表", "pro.balancesheet 逐股近 N 期资产负债表"),
    ("cashflow", "现金流量表", "pro.cashflow 逐股近 N 期现金流量表"),
    ("forecast", "业绩预告", "pro.forecast 逐股近 N 期业绩预告"),
]
for _d, _n, _desc in _FIN:
    _reg(_d, _n, _desc, "per_symbol", _per_symbol_fetch(_d))

_reg("pro_bar_daily", "前复权日线(pro_bar)",
     "ts.pro_bar(api=pro, adj=qfq) 逐股日线,窗口默认 dt 前 30 天",
     "per_symbol", fetch_pro_bar_daily)
