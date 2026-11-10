from fastapi.testclient import TestClient

from app.api.health import readiness
from app.main import app


def test_live_returns_200() -> None:
    with TestClient(app) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "live"}


def test_ready_fails_when_a_dependency_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("app.api.health.check_postgres", lambda: "fail")
    monkeypatch.setattr("app.api.health.check_redis", lambda: "ok")
    monkeypatch.setattr("app.api.health.check_bucket", lambda: "ok")
    monkeypatch.setattr("app.api.health.check_worker", lambda: "ok")

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"] == {
        "postgres": "fail",
        "redis": "ok",
        "s3": "ok",
        "worker": "ok",
    }
    assert "password" not in response.text.lower()
    assert "postgresql://" not in response.text


def test_ready_succeeds_when_checks_pass(monkeypatch) -> None:
    monkeypatch.setattr("app.api.health.check_postgres", lambda: "ok")
    monkeypatch.setattr("app.api.health.check_redis", lambda: "ok")
    monkeypatch.setattr("app.api.health.check_bucket", lambda: "ok")
    monkeypatch.setattr("app.api.health.check_worker", lambda: "ok")

    body = readiness()
    assert body.status == "ready"
    assert body.checks.model_dump() == {
        "postgres": "ok",
        "redis": "ok",
        "s3": "ok",
        "worker": "ok",
    }
