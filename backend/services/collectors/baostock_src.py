"""baostock 采集器:每次 fetch 内 login/logout(会话上下文);代码自动补 sh./sz. 前缀
(6 开头沪市);K 线 adjustflag=2(前复权);季频财务默认取 dt 的上一季度
(args.year/quarter 可覆盖)。baostock 返回值均为字符串,空串落 None。"""
from . import _common as c
from . import register
from .base import DataSet

K_FIELDS = {
    "d": "date,code,open,high,low,close,preclose,volume,amount,turn,"
         "tradestatus,pctChg,isST",
    "w": "date,code,open,high,low,close,volume,amount,turn,pctChg",
    "m": "date,code,open,high,low,close,volume,amount,turn,pctChg",
}
_FREQ_NAME = {"d": "daily", "w": "weekly", "m": "monthly"}


def _reg(dataset: str, name: str, desc: str, fetch) -> None:
    register(DataSet(
        key=f"baostock.{dataset}", source="baostock", name=name, module="baostock",
        desc=desc, mode="per_symbol", requires="package",
        target_table=f"ods_baostock_{dataset}", fetch=fetch))


def bs_code(sym: str) -> str:
    """代码归一化:XXXXXX.SH/XXXXXX.SZ(大小写均可)→ sh./sz.XXXXXX;
    纯 6 位代码 6 开头→sh.,否则 sz.;已带 sh./sz. 前缀等其余形态原样返回。"""
    s = str(sym).lower()
    if "." in s:
        code, _, mkt = s.partition(".")
        if mkt in ("sh", "sz") and code.isdigit():
            return f"{mkt}.{code}"
        return s
    return ("sh." if s.startswith("6") else "sz.") + s


def _login():
    import baostock as bs

    lr = bs.login()
    if lr is not None and str(getattr(lr, "error_code", "0")) != "0":
        raise RuntimeError(f"baostock 登录失败: {getattr(lr, 'error_msg', '')}")
    return bs


def _rows(rs) -> tuple[list[str], list[tuple]]:
    """ResultData → (fields, rows);error_code 非 0 抛错;空串→None。"""
    if str(getattr(rs, "error_code", "0")) != "0":
        raise RuntimeError(f"baostock 查询失败: {getattr(rs, 'error_msg', '')}")
    out = []
    while rs.next():
        out.append(tuple((v if v != "" else None) for v in rs.get_row_data()))
    return list(getattr(rs, "fields", []) or []), out


def _k_fetch(freq: str):
    def fetch(args, ctx):
        a = args or {}
        dt = c.ctx_dt(ctx)
        start = a.get("start_date") or c.days_before(dt, 30)
        end = a.get("end_date") or dt
        columns = ["symbol"] + K_FIELDS[freq].split(",")
        bs = _login()
        try:
            rows: list[tuple] = []
            for sym in c.iter_symbols(args):
                _, rws = _rows(bs.query_history_k_data_plus(
                    bs_code(sym), K_FIELDS[freq], start_date=start, end_date=end,
                    frequency=freq, adjustflag="2"))
                rows.extend((sym,) + r for r in rws)
            return columns, rows
        finally:
            bs.logout()
    return fetch


def _quarter_fetch(api_name: str):
    def fetch(args, ctx):
        a = args or {}
        dy, dq = c.prev_quarter(c.ctx_dt(ctx))
        year, quarter = int(a.get("year") or dy), int(a.get("quarter") or dq)
        bs = _login()
        try:
            columns, rows = None, []
            for sym in c.iter_symbols(args):
                fields, rws = _rows(getattr(bs, api_name)(
                    code=bs_code(sym), year=year, quarter=quarter))
                if columns is None and fields:
                    columns = ["symbol"] + fields
                rows.extend((sym,) + r for r in rws)
            return (columns or ["symbol"]), rows
        finally:
            bs.logout()
    return fetch


for _f in ("d", "w", "m"):
    _name = _FREQ_NAME[_f]
    _reg(f"history_k_{_name}", f"K线({_name})",
         f"query_history_k_data_plus frequency={_f} 前复权,窗口默认 dt 前 30 天",
         _k_fetch(_f))

_QUARTERLY = [
    ("profit", "query_profit_data", "盈利能力(季频)"),
    ("operation", "query_operation_data", "营运能力(季频)"),
    ("growth", "query_growth_data", "成长能力(季频)"),
    ("balance", "query_balance_data", "偿债能力(季频)"),
    ("cash_flow", "query_cash_flow_data", "现金流量(季频)"),
    ("dupont", "query_dupont_data", "杜邦指数(季频)"),
]
for _d, _api, _n in _QUARTERLY:
    _reg(_d, _n, f"bs.{_api} 逐股,默认 dt 上一季度(args.year/quarter 可覆盖)",
         _quarter_fetch(_api))
