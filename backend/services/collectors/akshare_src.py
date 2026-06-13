"""akshare 采集器:import akshare 延迟到 fetch 内(包缺失仅该源不可用)。
每个接口调用经 _ak_call 包裹:支持候选接口名列表(版本差异兼容),失败抛
RuntimeError 并带接口名与原因,单数据集失败原因可见。"""
from . import _common as c
from . import register
from .base import DataSet


def _reg(dataset: str, name: str, desc: str, mode: str, fetch) -> None:
    register(DataSet(
        key=f"akshare.{dataset}", source="akshare", name=name, module="akshare",
        desc=desc, mode=mode, requires="package",
        target_table=f"ods_akshare_{dataset}", fetch=fetch))


def _ak_call(names, **kwargs):
    """按候选接口名依次尝试调用 akshare;全部失败抛 RuntimeError(带原因)。"""
    import akshare as ak

    if isinstance(names, str):
        names = (names,)
    err = "无候选接口"
    for n in names:
        fn = getattr(ak, n, None)
        if fn is None:
            err = f"akshare 缺少接口 {n}"
            continue
        try:
            return fn(**kwargs)
        except Exception as e:  # noqa: BLE001
            err = f"{n} 调用失败: {e}"
    raise RuntimeError(f"akshare 采集失败: {err}")


def _snap(dataset, name, desc, api_names, kw=None):
    def fetch(args, ctx, _names=api_names, _kw=kw):
        kwargs = _kw(args or {}, ctx) if callable(_kw) else dict(_kw or {})
        return c.df_to_table(_ak_call(_names, **kwargs))
    _reg(dataset, name, desc, "snapshot", fetch)


def _date_kw(args, ctx):
    return {"date": c.nodash(c.ctx_dt(ctx))}


def fetch_margin(args, ctx):
    """融资融券:沪(stock_margin_sse)+深(stock_margin_szse)拼接,列并集对齐;
    两市接口均失败时退老版合并接口(stock_margin_sz_sh_daily)。"""
    import pandas as pd

    nd = c.nodash(c.ctx_dt(ctx))
    frames, errs = [], []
    for names, kwargs, market in (
            ("stock_margin_sse", {"start_date": nd, "end_date": nd}, "sse"),
            ("stock_margin_szse", {"date": nd}, "szse")):
        try:
            df = _ak_call(names, **kwargs)
            df.insert(0, "market", market)
            frames.append(df)
        except Exception as e:  # noqa: BLE001
            errs.append(str(e))
    if not frames:
        try:
            df = _ak_call(("stock_margin_sz_sh_daily", "stock_margin_account_info"))
        except RuntimeError as e:
            raise RuntimeError(f"融资融券各接口均失败: {'; '.join(errs)}; {e}") from e
        df.insert(0, "market", "all")
        frames.append(df)
    merged = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    return c.df_to_table(merged)


def _hist_window(args, ctx) -> tuple[str, str]:
    dt = c.ctx_dt(ctx)
    a = args or {}
    return (c.nodash(a.get("start_date") or c.days_before(dt, 30)),
            c.nodash(a.get("end_date") or dt))


def fetch_stock_zh_a_hist(args, ctx):
    start, end = _hist_window(args, ctx)
    return c.per_symbol_df(args, lambda sym: _ak_call(
        "stock_zh_a_hist", symbol=sym, period="daily",
        start_date=start, end_date=end, adjust="qfq"))


def _ff_market(sym: str) -> str:
    code = sym.split(".")[0]
    return "sh" if code.startswith("6") else ("bj" if code[:1] in ("4", "8") else "sz")


def fetch_individual_fund_flow(args, ctx):
    return c.per_symbol_df(args, lambda sym: _ak_call(
        "stock_individual_fund_flow", stock=sym.split(".")[0],
        market=_ff_market(sym)))


def fetch_board_industry_hist(args, ctx):
    """行业板块历史行情:symbols 传板块名列表(如 ["小金属", "银行"])。"""
    start, end = _hist_window(args, ctx)
    return c.per_symbol_df(args, lambda sym: _ak_call(
        "stock_board_industry_hist_em", symbol=sym, start_date=start,
        end_date=end, period="日k", adjust=""))


_snap("stock_zh_a_spot_em", "沪深京A股实时行情",
      "ak.stock_zh_a_spot_em 全市场实时快照", "stock_zh_a_spot_em")
_snap("stock_board_industry_name_em", "行业板块列表",
      "ak.stock_board_industry_name_em 东财行业板块", "stock_board_industry_name_em")
_snap("stock_board_concept_name_em", "概念板块列表",
      "ak.stock_board_concept_name_em 东财概念板块", "stock_board_concept_name_em")
_snap("stock_market_fund_flow", "大盘资金流(历史全量)",
      "ak.stock_market_fund_flow 沪深两市资金流历史", "stock_market_fund_flow")
_snap("stock_sector_fund_flow_rank", "行业资金流排名",
      "ak.stock_sector_fund_flow_rank(今日/行业资金流)", "stock_sector_fund_flow_rank",
      kw=lambda a, ctx: {"indicator": a.get("indicator", "今日"),
                         "sector_type": a.get("sector_type", "行业资金流")})
_snap("stock_zt_pool_em", "涨停股池", "ak.stock_zt_pool_em(date=dt)",
      "stock_zt_pool_em", kw=_date_kw)
_snap("stock_zt_pool_strong_em", "强势股池", "ak.stock_zt_pool_strong_em(date=dt)",
      "stock_zt_pool_strong_em", kw=_date_kw)
_snap("stock_zt_pool_dtgc_em", "跌停股池", "ak.stock_zt_pool_dtgc_em(date=dt)",
      "stock_zt_pool_dtgc_em", kw=_date_kw)
_snap("stock_zt_pool_zbgc_em", "炸板股池", "ak.stock_zt_pool_zbgc_em(date=dt)",
      "stock_zt_pool_zbgc_em", kw=_date_kw)
_snap("stock_hot_rank_em", "股票热度排名", "ak.stock_hot_rank_em 东财人气榜",
      "stock_hot_rank_em")
_snap("stock_market_activity_legu", "市场赚钱效应(乐咕)",
      "ak.stock_market_activity_legu(缺失时尝试 *_em 变体)",
      ("stock_market_activity_legu", "stock_market_activity_em"))
_snap("stock_hsgt_fund_summary", "沪深港通资金流向",
      "ak.stock_hsgt_fund_flow_summary_em 沪深港通资金流向汇总",
      "stock_hsgt_fund_flow_summary_em")
_reg("margin_sz_sh_daily", "融资融券(沪深)",
     "stock_margin_sse+szse 拼接,版本差异 try 兼容", "snapshot", fetch_margin)

_reg("stock_zh_a_hist", "个股历史行情(前复权)",
     "ak.stock_zh_a_hist 逐股日线 qfq,窗口默认 dt 前 30 天",
     "per_symbol", fetch_stock_zh_a_hist)
_reg("stock_individual_fund_flow", "个股资金流",
     "ak.stock_individual_fund_flow 逐股(自动判沪/深/京)",
     "per_symbol", fetch_individual_fund_flow)
_reg("stock_board_industry_hist_em", "行业板块历史行情",
     "ak.stock_board_industry_hist_em 逐板块日K(symbols 传板块名)",
     "per_symbol", fetch_board_industry_hist)
