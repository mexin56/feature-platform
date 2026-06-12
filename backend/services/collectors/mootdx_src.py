"""mootdx 采集器(通达信协议):实时五档快照(80 只/批)与财务快照(逐股)。
import 延迟到 fetch 内,包缺失仅该源不可用;客户端尽力关闭(close/exit 兼容)。"""
from . import _common as c
from . import register
from .base import DataSet

L5_BATCH = 80


def _client():
    from mootdx.quotes import Quotes

    return Quotes.factory(market="std")


def _close(cli) -> None:
    try:
        close = getattr(cli, "close", None) or getattr(cli, "exit", None)
        if callable(close):
            close()
    except Exception:  # noqa: BLE001
        pass


def fetch_quotes_l5(args, ctx):
    """实时五档:symbols 按 80 只/批调用 quotes,批间 sleep interval_sec。"""
    import pandas as pd

    symbols = c.require_symbols(args)
    batches = [symbols[i:i + L5_BATCH] for i in range(0, len(symbols), L5_BATCH)]
    cli = _client()
    try:
        frames = []
        for batch in c.iter_with_interval(batches, args):
            df = cli.quotes(symbol=batch)
            if df is not None and len(df):
                frames.append(df)
    finally:
        _close(cli)
    if not frames:
        raise RuntimeError("mootdx 实时五档未返回任何数据(检查行情服务器连通性)")
    merged = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    return c.df_to_table(merged)


def fetch_finance(args, ctx):
    """财务快照:逐股 client.finance(symbol=...)。"""
    cli = _client()
    try:
        return c.per_symbol_df(args, lambda sym: cli.finance(symbol=sym.split(".")[0]))
    finally:
        _close(cli)


def _reg(dataset: str, name: str, desc: str, fetch) -> None:
    register(DataSet(
        key=f"mootdx.{dataset}", source="mootdx", name=name, module="mootdx",
        desc=desc, mode="per_symbol", requires="package",
        target_table=f"ods_mootdx_{dataset}", fetch=fetch))


_reg("quotes_l5", "实时五档行情",
     "Quotes.factory(std).quotes 80 只/批,批间限频", fetch_quotes_l5)
_reg("finance", "财务数据快照", "client.finance 逐股财务概要", fetch_finance)
