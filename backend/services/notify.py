"""Webhook 通知:飞书机器人卡片格式。发送失败只记日志,绝不影响主流程。"""
import traceback

import httpx


def send_webhook(url: str, title: str, text: str) -> None:
    if not url:
        return
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": title},
                       "template": "red" if "失败" in title or "超时" in title else "blue"},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": text}}],
        },
    }
    try:
        httpx.post(url, json=payload, timeout=5)
    except Exception:  # noqa: BLE001  通知失败不影响主流程
        traceback.print_exc()


def get_setting(db, key: str, default: str = "") -> str:
    from sqlalchemy import select

    from ..models import SystemSetting

    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    return row.value if row else default
