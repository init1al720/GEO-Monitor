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


def test_ready_reports_dependency_checks(client: TestClient) -> None:
    response = client.get("/api/v1/ready")

    body = response.json()
    checks = body["data"]["checks"]
    assert "database" in checks
    assert "schema" in checks
    assert "auth" in checks
    # 表没建的环境要返回 503，不能假报就绪骗过负载均衡
    assert response.status_code in (200, 503)
    assert body["data"]["ready"] is (response.status_code == 200)


def test_ready_does_not_leak_secrets(client: TestClient) -> None:
    text = client.get("/api/v1/ready").text

    assert "api_key" not in text.lower()
    assert "password" not in text.lower()
    assert "postgresql" not in text.lower()
