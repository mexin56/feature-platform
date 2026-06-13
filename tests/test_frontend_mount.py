"""
Test that the frontend dist is correctly mounted when present.
If frontend/dist/index.html does not exist, the test is skipped.
"""
import pathlib

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings

_DIST = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "dist"


def _dist_ready():
    return (_DIST / "index.html").exists()


@pytest.mark.skipif(not _dist_ready(), reason="frontend/dist/index.html not built yet")
def test_spa_root_returns_html(tmp_path):
    """GET / should return 200 text/html (SPA index)."""
    app = create_app(Settings(storage_dir=str(tmp_path), sync_scheduler=True))
    with TestClient(app, raise_server_exceptions=True) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        # index.html 必须 no-cache,否则部署后浏览器用旧 index 指向旧 bundle,页面不更新
        assert "no-cache" in resp.headers.get("cache-control", "")
