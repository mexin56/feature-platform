"""采集器公共工具:ctx 采集日期、DataFrame→(columns, rows) 转换、逐股循环骨架。
per_symbol 数据集统一契约:args.symbols 必填非空列表;调用间 sleep
args.interval_sec(默认 0.5s)防限频;行首统一前置 symbol 列。"""
import math
import re
import time
from datetime import date, datetime, timedelta

DEFAULT_INTERVAL = 0.5
_COL_BAD_RE = re.compile(r"[^0-9A-Za-z_一-鿿]")


def ctx_dt(ctx: dict) -> str:
    """采集日期 YYYY-MM-DD,与 data_collect 插件 dt 口径一致(data_interval_end 的日期)。"""
    end = (ctx or {}).get("data_interval_end")
    return end[:10] if end else datetime.now().strftime("%Y-%m-%d")


def nodash(d: str) -> str:
    return str(d).replace("-", "")


def days_before(d: str, n: int) -> str:
    return (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=n)).strftime("%Y-%m-%d")


def days_after(d: str, n: int) -> str:
    return (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def prev_quarter(d: str) -> tuple[int, int]:
    """dt 所在季度的上一季度 (year, quarter)——季频财务数据集默认取数口径。"""
    y, q = int(d[:4]), (int(d[5:7]) - 1) // 3 + 1
    return (y, q - 1) if q > 1 else (y - 1, 4)


def to_scalar(v):
    """任意标量 → JSON/duckdb 安全值(str/int/float/bool/None);日期转 YYYY-MM-DD。"""
    if v is None:
        return None
    try:
        if v != v:  # NaN/NaT 自身不等
            return None
    except Exception:  # noqa: BLE001
        pass
    if isinstance(v, bool):
        return bool(v)
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else float(v)
    if isinstance(v, int):
        return int(v)
    if isinstance(v, str):
        return v
    if isinstance(v, datetime):
        if not (v.hour or v.minute or v.second):
            return v.strftime("%Y-%m-%d")
        return v.isoformat()
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    item = getattr(v, "item", None)  # numpy/pandas 标量 → python 原生
    if callable(item):
        try:
            got = item()
        except Exception:  # noqa: BLE001
            return str(v)
        if got is not v:
            return to_scalar(got)
    return str(v)


def safe_columns(cols) -> list[str]:
    """列名清洗:仅保留字母/数字/下划线/汉字(其余替换 _),空名兜底 col{i},重名加序号。"""
    out, seen = [], {}
    for i, c in enumerate(cols):
        s = _COL_BAD_RE.sub("_", str(c)).strip("_") or f"col{i}"
        if s in seen:
            seen[s] += 1
            s = f"{s}_{seen[s]}"
        else:
            seen[s] = 0
        out.append(s)
    return out


def df_to_table(df) -> tuple[list[str], list[tuple]]:
    """pandas DataFrame → (columns, rows);NaN/NaT→None,值转原生标量,列名清洗。"""
    import pandas as pd

    obj = df.astype(object).where(pd.notnull(df), None)
    columns = safe_columns(obj.columns)
    rows = [tuple(to_scalar(v) for v in t)
            for t in obj.itertuples(index=False, name=None)]
    return columns, rows


def require_symbols(args: dict) -> list[str]:
    symbols = (args or {}).get("symbols")
    if not isinstance(symbols, (list, tuple)) or not symbols:
        raise RuntimeError("per_symbol 数据集必须提供非空 args.symbols 列表")
    return [str(s) for s in symbols]


def iter_with_interval(items, args: dict):
    """逐项迭代,项间 sleep args.interval_sec(默认 0.5s)防限频。"""
    interval = float((args or {}).get("interval_sec", DEFAULT_INTERVAL))
    for i, it in enumerate(items):
        if i:
            time.sleep(interval)
        yield it


def iter_symbols(args: dict):
    yield from iter_with_interval(require_symbols(args), args)


def per_symbol_df(args: dict, fetch_one) -> tuple[list[str], list[tuple]]:
    """逐股取 DataFrame 累积成一张表:行首前置 symbol 列;列以首个非空结果为准,
    后续结果缺列补 None、多余列丢弃(防 API 版本差异拖垮整批)。"""
    columns: list[str] | None = None
    rows: list[tuple] = []
    for sym in iter_symbols(args):
        df = fetch_one(sym)
        if df is None or len(df) == 0:
            continue
        cols, rws = df_to_table(df)
        cols = [("src_" + c) if c == "symbol" else c for c in cols]
        if columns is None:
            columns = ["symbol"] + cols
            rows.extend((sym,) + r for r in rws)
        else:
            idx = {c: i for i, c in enumerate(cols)}
            for r in rws:
                rows.append((sym,) + tuple(
                    r[idx[c]] if c in idx else None for c in columns[1:]))
    return (columns or ["symbol"]), rows
