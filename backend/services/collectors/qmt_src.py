"""QMT(迅投 miniQMT/xtquant)目录条目:仅编目不实现采集。
全部 fetch=None、requires="terminal"——QMT 数据须在装有 QMT 终端的机器上
经 xtquant 访问,平台侧不直接采集(available() 恒为 False 并给出原因)。"""
from . import register
from .base import DataSet

_NOTE = "需本机 QMT 终端(xtquant),未实现"

_ENTRIES = [
    ("full_tick", "全推行情 tick 快照", f"xtdata.get_full_tick 全市场最新 tick;{_NOTE}"),
    ("market_data_1m", "K线(1分钟)", f"xtdata.get_market_data period=1m;{_NOTE}"),
    ("market_data_1d", "K线(日线)", f"xtdata.get_market_data period=1d;{_NOTE}"),
    ("l2_quote", "Level2 十档盘口", f"xtdata l2quote 十档行情快照;{_NOTE}"),
    ("l2_order", "Level2 逐笔委托", f"xtdata l2order 逐笔委托流;{_NOTE}"),
    ("l2_transaction", "Level2 逐笔成交", f"xtdata l2transaction 逐笔成交流;{_NOTE}"),
    ("margin_data", "融资融券明细", f"xtdata 两融数据;{_NOTE}"),
    ("hsgt_data", "沪深港通资金/持股", f"xtdata 北向南向资金与持股;{_NOTE}"),
]

for _d, _n, _desc in _ENTRIES:
    register(DataSet(
        key=f"qmt.{_d}", source="qmt", name=_n, module="xtquant", desc=_desc,
        mode="snapshot", requires="terminal", target_table=f"ods_qmt_{_d}",
        fetch=None))
