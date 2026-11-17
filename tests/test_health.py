"""系统接口与统一响应/异常的冒烟测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "ok"
    assert body["data"]["status"] == "ok"
    assert body["request_id"]


def test_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"X-Request-ID": "trace-abc"})

    assert response.headers["X-Request-ID"] == "trace-abc"
    assert response.json()["request_id"] == "trace-abc"


def test_unknown_route_uses_unified_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "http_404"
    assert "request_id" in body
