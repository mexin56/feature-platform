"""模板变量渲染:同一条 SQL/脚本参数既能日常调度也能补数。
变量:ds / ds_nodash(= data_interval_start 日期)、data_interval_start / data_interval_end。"""
import re
from datetime import datetime

_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def build_context(data_interval_start: datetime, data_interval_end: datetime) -> dict:
    return {
        "ds": data_interval_start.strftime("%Y-%m-%d"),
        "ds_nodash": data_interval_start.strftime("%Y%m%d"),
        "data_interval_start": data_interval_start.strftime("%Y-%m-%d %H:%M:%S"),
        "data_interval_end": data_interval_end.strftime("%Y-%m-%d %H:%M:%S"),
    }


def render(text: str, ctx: dict) -> str:
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name not in ctx:
            raise ValueError(f"未知模板变量: {name}")
        return str(ctx[name])

    return _PATTERN.sub(repl, text)
