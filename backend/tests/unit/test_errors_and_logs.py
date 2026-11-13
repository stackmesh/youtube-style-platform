import logging

from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.exceptions import AppError
from app.main import app


def test_request_id_is_logged_and_returned(caplog) -> None:
    caplog.set_level(logging.INFO)
    with TestClient(app) as client:
        response = client.get("/health/live", headers={"X-Request-ID": "req-stage1"})
    assert response.headers["X-Request-ID"] == "req-stage1"
    matched = [
        record for record in caplog.records if getattr(record, "request_id", None) == "req-stage1"
    ]
    assert matched
    assert "request completed" in matched[-1].getMessage()


def test_unknown_route_uses_error_format() -> None:
    with TestClient(app) as client:
        response = client.get("/missing")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"] == "Not found."
    assert body["error"]["details"] == []
    assert body["error"]["request_id"]


def test_validation_error_hides_input_values() -> None:
    class Payload(BaseModel):
        name: str

    @app.post("/_test/validate")
    def validate(payload: Payload) -> dict[str, str]:
        return {"name": payload.name}

    with TestClient(app) as client:
        response = client.post("/_test/validate", json={})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"]
    assert "input" not in response.text


def test_unexpected_error_hides_exception_text() -> None:
    @app.get("/_test/boom")
    def boom() -> None:
        raise RuntimeError("postgres password super-secret")

    with TestClient(app) as client:
        response = client.get("/_test/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["request_id"]
    assert "super-secret" not in response.text


def test_app_error_shape() -> None:
    @app.get("/_test/app-error")
    def app_error() -> None:
        raise AppError("VIDEO_NOT_FOUND", "Video not found.", 404)

    with TestClient(app) as client:
        response = client.get("/_test/app-error")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "VIDEO_NOT_FOUND"


def test_oversized_json_is_rejected_without_echoing_the_body(caplog) -> None:
    caplog.set_level(logging.INFO)
    marker = "BODYMARKER" + ("x" * 300_000)
    payload = b'{"email":"a@example.com","password":"' + marker.encode() + b'"}'
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login",
            content=payload,
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert "BODYMARKER" not in response.text
    assert "BODYMARKER" not in " ".join(record.getMessage() for record in caplog.records)


def test_untrusted_host_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("TRUSTED_HOSTS", "videos.example")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            rejected = client.get("/health/live", headers={"Host": "evil.example"})
            allowed = client.get("/health/live", headers={"Host": "videos.example"})
    finally:
        get_settings.cache_clear()
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "UNTRUSTED_HOST"
    assert "evil.example" not in rejected.text
    assert allowed.status_code == 200


def test_docs_are_hidden_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("DOCS_ENABLED", "false")
    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    try:
        fresh = create_app()
        with TestClient(fresh) as client:
            response = client.get("/docs")
    finally:
        get_settings.cache_clear()
    assert response.status_code == 404


def test_openapi_lists_health_routes() -> None:
    schema = app.openapi()
    assert "/health/live" in schema["paths"]
    assert "/health/ready" in schema["paths"]
