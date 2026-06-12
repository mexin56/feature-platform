"""Webhook 通知:飞书机器人卡片格式。发送失败只记日志,绝不影响主流程。"""
import threading
import traceback

import httpx


def send_webhook(url: str, title: str, text: str) -> None:
    """异步发送(守护线程发后即忘):调度 tick 内调用不被慢网络阻塞。"""
    if not url:
        return
    threading.Thread(target=_post_card, args=(url, title, text), daemon=True).start()


def _post_card(url: str, title: str, text: str) -> None:
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
