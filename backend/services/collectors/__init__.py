"""采集器目录:各源模块导入时经 register() 注册数据集到 CATALOG。
单个采集器模块导入失败只损失该源条目,不拖垮整个目录(try/except 包裹)。"""
import importlib
import traceback

from .base import DataSet, available  # noqa: F401  消费方统一从包根导入

CATALOG: dict[str, DataSet] = {}


def register(ds: DataSet) -> None:
    CATALOG[ds.key] = ds


# T2 起追加:sina/eastmoney/ths/cninfo/akshare_src/baostock_src/mootdx_src/tushare_src/qmt_src
_COLLECTOR_MODULES = ("tencent",)

for _m in _COLLECTOR_MODULES:
    try:
        importlib.import_module(f".{_m}", __name__)
    except Exception:  # noqa: BLE001  坏模块仅打印,目录保持可用
        traceback.print_exc()
