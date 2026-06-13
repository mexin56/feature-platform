"""爱问财(同花顺问财/wencai)采集器:自然语言选股,pywencai.get(query) → DataFrame。
import pywencai 延迟到 _run_query 内(包缺失仅该源不可用;另需本机 node.js)。
问财列名带日期后缀方括号(如 涨停[20260612]),会破坏 writer COL_RE,故采集时清洗列名。"""
import re

from . import _common as c
from . import register
from .base import DataSet

# 列名尾部的 [..] 后缀段(问财按查询日给列加日期后缀);剥离后再做字符清洗
_BRACKET_RE = re.compile(r"\[[^\[\]]*\]\s*$")
_COL_BAD_RE = re.compile(r"[^0-9A-Za-z_一-鿿]")
_REPEAT_US_RE = re.compile(r"_{2,}")


def _sanitize_cols(cols) -> list[str]:
    """问财列名清洗:剥离尾部 [..] 日期后缀,非 [字母数字下划线汉字] 替换为 _,
    折叠连续 _、去首尾 _;空名兜底 col{i};重名加 _2/_3 去重。结果均满足 writer COL_RE。"""
    out: list[str] = []
    seen: dict[str, int] = {}
    for i, col in enumerate(cols):
        s = _BRACKET_RE.sub("", str(col))
        s = _COL_BAD_RE.sub("_", s)
        s = _REPEAT_US_RE.sub("_", s).strip("_") or f"col{i}"
        if s in seen:
            seen[s] += 1
            s = f"{s}_{seen[s] + 1}"
        else:
            seen[s] = 0
        out.append(s)
    return out


def _run_query(query: str, ctx: dict, loop=False) -> tuple[list[str], list[tuple]]:
    """渲染 query 中 {dt}/{dt_nodash} 占位 → pywencai.get(query, loop) → (cols, rows)。
    返回 None 或非 DataFrame(部分 query_type 返回 dict)时抛可读 RuntimeError;列名清洗。"""
    import pandas as pd
    import pywencai

    dt = c.ctx_dt(ctx)
    rendered = (str(query or "")
                .replace("{dt}", dt)
                .replace("{dt_nodash}", c.nodash(dt)))
    df = pywencai.get(query=rendered, loop=loop)
    if df is None or not isinstance(df, pd.DataFrame):
        raise RuntimeError("爱问财查询无结果或返回非表格(query 可能需更具体)")
    obj = df.astype(object).where(pd.notnull(df), None)
    columns = _sanitize_cols(obj.columns)
    rows = [tuple(c.to_scalar(v) for v in t)
            for t in obj.itertuples(index=False, name=None)]
    return columns, rows


register(DataSet(
    key="wencai.zt_pool", source="wencai", name="问财-今日涨停", module="pywencai",
    desc="同花顺问财自然语言选股,需 pywencai+node.js(query='今日涨停 非ST')",
    mode="snapshot", requires="package", target_table="ods_wencai_zt_pool",
    fetch=lambda args, ctx: _run_query("今日涨停 非ST", ctx)))
