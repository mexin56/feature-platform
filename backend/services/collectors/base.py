"""数据集目录基础类型:DataSet 描述一个可采集数据集;available() 判定运行环境可用性。
fetch 约定:fetch(args, ctx) -> (columns, rows);snapshot 一次取全市场,per_symbol 逐股循环。"""
import importlib.util
from dataclasses import dataclass
from typing import Callable

# fetch(args: dict, ctx: dict) -> (列名列表, 行元组列表)
FetchFn = Callable[[dict, dict], tuple[list[str], list[tuple]]]


@dataclass
class DataSet:
    key: str               # "source.dataset",目录唯一键
    source: str            # 数据源标识:tencent/sina/eastmoney/...
    name: str              # 中文名(目录展示)
    module: str            # 依赖的 python 包名(requires=package 时检测)
    desc: str
    mode: str              # snapshot(一次全市场) | per_symbol(逐股循环)
    requires: str | None   # None | token | package | terminal
    target_table: str      # ods_{source}_{dataset}
    fetch: FetchFn | None = None  # None = 采集未实现(仅目录条目)


def available(ds: DataSet) -> tuple[bool, str]:
    """可用性判定:(是否可采集, 不可用原因)。
    token 数据集视同仅需 tushare 包(tushare_client 内置默认 token 与专用网关)。"""
    if ds.requires == "terminal":
        return False, "需本机 QMT 终端,平台不直接采集"
    if ds.fetch is None:
        return False, "采集未实现"
    if ds.requires in ("package", "token"):
        pkg = "tushare" if ds.requires == "token" else ds.module
        if importlib.util.find_spec(pkg) is None:
            return False, f"缺少 python 包: {pkg}"
    return True, ""
