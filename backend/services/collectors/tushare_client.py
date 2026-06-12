"""tushare 全局唯一初始化入口(用户指定的专用调用方式,勿改动)。

调用约定(任何 tushare 采集必须经由本文件):
    from backend.services.collectors.tushare_client import get_pro, pro_bar
    pro = get_pro()
    df = pro.daily(trade_date="20260611")
    df = pro_bar(pro, ts_code="000001.SZ", limit=3)   # pro_bar 必须传 api=pro

⭐ 若报「Token 不对」,检查是否丢了 _DataApi__http_url 这一行——
   本入口使用专用网关,必须覆盖默认 http_url。
"""

DEFAULT_TOKEN = "b8678127ca2a1982933d0265b3b80e2da114ee55d766fa4fafaaf23c"
HTTP_URL = "http://tt.dailyfetch.top/"


def get_pro(token: str | None = None):
    """返回已配置专用网关的 tushare pro 客户端。

    token 优先级:显式入参 > SystemSetting(tushare_token,由调用方传入) > 内置默认。
    """
    import tushare as ts

    pro = ts.pro_api(token or DEFAULT_TOKEN)
    pro._DataApi__http_url = HTTP_URL  # 必须:专用网关,缺失会报 Token 不对
    return pro


def pro_bar(pro, **kwargs):
    """pro_bar 必须以 api=pro 方式调用,统一封装。"""
    import tushare as ts

    return ts.pro_bar(api=pro, **kwargs)
