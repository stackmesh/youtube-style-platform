"""Rate limits and fail-closed behavior for the hardening pass."""

import uuid

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from app.infrastructure.redis import redis_client
from app.main import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clear_rate_limits():
    _clear()
    yield
    _clear()


def _clear() -> None:
    cache = redis_client()
    keys = list(cache.scan_iter("rl:*"))
    if keys:
        cache.delete(*keys)


def _register(client: TestClient) -> dict[str, str]:
    suffix = uuid.uuid4().hex[:8]
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"hard-{suffix}@example.com",
            "password": "correct-password",
            "handle": f"h{suffix}",
            "name": "Hardening",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return {"token": body["access_token"], "id": body["user"]["id"]}


def test_login_and_search_return_retry_after(client: TestClient) -> None:
    for _ in range(10):
        attempt = client.post(
            "/api/v1/auth/login",
            json={"email": "missing@example.com", "password": "correct-password"},
        )
        assert attempt.status_code == 401
    limited = client.post(
        "/api/v1/auth/login",
        json={"email": "missing@example.com", "password": "correct-password"},
    )
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert int(limited.headers["retry-after"]) > 0
    _clear()
    for _ in range(60):
        found = client.get("/api/v1/search", params={"q": "a"})
        assert found.status_code == 200
    blocked = client.get("/api/v1/search", params={"q": "a"})
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"]


def test_upload_initiate_is_limited_and_fails_closed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _register(client)
    created = client.post(
        "/api/v1/videos",
        headers={"Authorization": f"Bearer {owner['token']}"},
        json={"title": "limit me", "description": "", "tags": [], "visibility": "private"},
    )
    assert created.status_code == 200, created.text
    video_id = created.json()["id"]
    body = {"byte_size": 1024, "content_type": "video/mp4"}
    headers = {"Authorization": f"Bearer {owner['token']}"}
    for _ in range(10):
        started = client.post(f"/api/v1/videos/{video_id}/upload", headers=headers, json=body)
        assert started.status_code in {200, 409}
    limited = client.post(f"/api/v1/videos/{video_id}/upload", headers=headers, json=body)
    assert limited.status_code == 429, limited.text
    assert limited.headers["retry-after"]

    def broken():
        raise RedisError("down")

    monkeypatch.setattr("app.core.rate_limit.redis_client", broken)
    closed = client.post(f"/api/v1/videos/{video_id}/upload", headers=headers, json=body)
    assert closed.status_code == 503
    assert closed.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


def test_authenticated_requests_have_a_per_user_backstop(client: TestClient) -> None:
    owner = _register(client)
    headers = {"Authorization": f"Bearer {owner['token']}"}
    for _ in range(120):
        response = client.get("/api/v1/auth/me", headers=headers)
        assert response.status_code == 200
    limited = client.get("/api/v1/auth/me", headers=headers)
    assert limited.status_code == 429
    assert limited.headers["retry-after"]
