"""示例模块：验证「路由 → 服务 → 统一响应/异常」这条链路。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_create_and_read_item(client: TestClient) -> None:
    created = client.post("/api/v1/examples", json={"name": "冒烟用例", "description": "hello"})
    assert created.status_code == 201
    item = created.json()["data"]
    assert item["name"] == "冒烟用例"

    fetched = client.get(f"/api/v1/examples/{item['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["data"]["id"] == item["id"]


def test_list_items_is_paginated(client: TestClient) -> None:
    client.post("/api/v1/examples", json={"name": "分页用例"})

    response = client.get("/api/v1/examples", params={"page": 1, "page_size": 10})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["meta"]["page"] == 1
    assert data["meta"]["total"] >= 1
    assert isinstance(data["items"], list)


def test_missing_item_raises_business_error(client: TestClient) -> None:
    response = client.get("/api/v1/examples/not-exist")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found"
    assert body["data"]["id"] == "not-exist"


def test_validation_error_is_normalized(client: TestClient) -> None:
    response = client.post("/api/v1/examples", json={"name": ""})

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_delete_item(client: TestClient) -> None:
    item_id = client.post("/api/v1/examples", json={"name": "待删除"}).json()["data"]["id"]

    assert client.delete(f"/api/v1/examples/{item_id}").status_code == 200
    assert client.get(f"/api/v1/examples/{item_id}").status_code == 404
