"""跨域白名单。前端在 Vercel，预览域名每次部署都变，靠正则兜住。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings


@pytest.fixture
def vercel_app() -> Iterator[TestClient]:
    """按「生产配置」重新造一个 app。

    settings 是 lru_cache 的单例，改环境变量后必须清缓存才会生效；
    测试结束也要再清一次，否则后面的用例会用到这里的配置。
    """
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("CORS_ORIGINS", "https://geo-monitor.vercel.app")
    monkeypatch.setenv("CORS_ORIGIN_REGEX", r"^https://geo-monitor(-[a-z0-9-]+)?\.vercel\.app$")
    get_settings.cache_clear()

    from app.main import create_app

    try:
        with TestClient(create_app()) as client:
            yield client
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_preview_domain_is_allowed(vercel_app: TestClient) -> None:
    origin = "https://geo-monitor-git-feat-login-abc123.vercel.app"

    resp = vercel_app.get("/api/v1/health", headers={"Origin": origin})

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == origin


def test_production_domain_is_allowed(vercel_app: TestClient) -> None:
    origin = "https://geo-monitor.vercel.app"

    resp = vercel_app.get("/api/v1/health", headers={"Origin": origin})

    assert resp.headers["access-control-allow-origin"] == origin


def test_other_domain_is_rejected(vercel_app: TestClient) -> None:
    """别人仿一个 geo-monitor-x.vercel.app.evil.com 不能放进来。"""
    resp = vercel_app.get(
        "/api/v1/health", headers={"Origin": "https://geo-monitor-x.vercel.app.evil.com"}
    )

    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers
