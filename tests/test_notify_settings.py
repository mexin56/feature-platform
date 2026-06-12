import threading
import time

from backend.services import notify


def test_send_webhook_posts_card(monkeypatch):
    sent = {}

    def fake_post(url, json=None, timeout=None):
        sent.update(url=url, json=json)

        class R:
            status_code = 200

        return R()

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    notify._post_card("https://hook", "标题", "正文内容")
    assert sent["url"] == "https://hook"
    assert "标题" in str(sent["json"])


def test_send_webhook_swallows_errors(monkeypatch):
    def boom(*a, **kw):
        raise OSError("network down")

    monkeypatch.setattr(notify.httpx, "post", boom)
    notify._post_card("https://hook", "t", "d")  # 不应抛异常


def test_send_webhook_noop_without_url():
    notify.send_webhook("", "t", "d")  # 空 URL 直接返回


def test_send_webhook_async_nonblocking(monkeypatch):
    done = threading.Event()

    def slow_post(url, json=None, timeout=None):
        time.sleep(0.5)
        done.set()

    monkeypatch.setattr(notify.httpx, "post", slow_post)
    t0 = time.monotonic()
    notify.send_webhook("https://hook", "t", "d")
    assert time.monotonic() - t0 < 0.3  # 立即返回,不等待发送
    assert done.wait(3)  # 后台线程最终执行


def test_settings_api_admin_only(client, admin_headers):
    r = client.put("/api/settings/webhook_url",
                   json={"value": "https://open.feishu.cn/x"}, headers=admin_headers)
    assert r.status_code == 200
    assert client.get("/api/settings/webhook_url",
                      headers=admin_headers).json()["value"].endswith("/x")
    # 覆盖更新
    client.put("/api/settings/webhook_url", json={"value": "https://b"}, headers=admin_headers)
    assert client.get("/api/settings/webhook_url",
                      headers=admin_headers).json()["value"] == "https://b"
    # 非管理员拒绝
    client.post("/api/users", json={"username": "dev", "password": "dev123456",
                                    "role": "developer"}, headers=admin_headers)
    rr = client.post("/api/auth/login", json={"username": "dev", "password": "dev123456"})
    dev = {"Authorization": f"Bearer {rr.json()['token']}"}
    assert client.get("/api/settings/webhook_url", headers=dev).status_code == 403
    assert client.put("/api/settings/webhook_url", json={"value": "x"},
                      headers=dev).status_code == 403


def test_unknown_setting_key_rejected(client, admin_headers):
    assert client.put("/api/settings/nonsense", json={"value": "1"},
                      headers=admin_headers).status_code == 400
