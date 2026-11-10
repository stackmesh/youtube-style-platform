import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from app.api.deps import require_admin, require_moderator
from app.core.config import get_settings
from app.db.migrate import upgrade_head
from app.db.models import RefreshToken, User, UserRole
from app.db.session import SessionLocal
from app.identity.service import deactivate_user, ensure_bootstrap_admin
from app.infrastructure.redis import redis_client
from app.main import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clear_rate_limits() -> None:
    cache = redis_client()
    keys = list(cache.scan_iter("rl:*"))
    if keys:
        cache.delete(*keys)


def _install_role_probes() -> None:
    if any(getattr(route, "path", None) == "/api/v1/_test/admin" for route in app.routes):
        return
    probe = APIRouter()

    @probe.get("/api/v1/_test/admin")
    def admin_probe(user: User = Depends(require_admin)) -> dict[str, str]:
        return {"role": user.role.value}

    @probe.get("/api/v1/_test/moderator")
    def moderator_probe(user: User = Depends(require_moderator)) -> dict[str, str]:
        return {"role": user.role.value}

    app.include_router(probe)


def _use_refresh(client: TestClient, token: str) -> None:
    client.cookies.clear()
    client.cookies.set("refresh_token", token, path="/")


def test_register_login_channel_and_role_are_enforced(client: TestClient) -> None:
    email = f"person-{uuid.uuid4().hex[:8]}@example.com"
    handle = f"s{uuid.uuid4().hex[:8]}"
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "email": email.upper(),
            "password": "correct-password",
            "handle": handle.upper(),
            "name": "Studio",
            "role": "ADMIN",
        },
    )
    assert registered.status_code == 200, registered.text
    body = registered.json()
    assert body["user"]["role"] == "USER"
    assert body["user"]["channel"]["handle"] == handle
    assert "password_hash" not in registered.text
    assert "refresh_token" not in body
    assert "refresh_token" in registered.cookies

    session = SessionLocal()
    try:
        user = session.scalar(select(User).where(User.email == email))
        assert user is not None
        assert user.role is UserRole.USER
        assert user.password_hash.startswith("$argon2id$")
        stored = session.scalar(select(RefreshToken).where(RefreshToken.user_id == user.id))
        assert stored is not None
        assert stored.token_hash != registered.cookies["refresh_token"].encode()
    finally:
        session.close()

    duplicate_email = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "correct-password",
            "handle": "other_one",
            "name": "Other",
        },
    )
    assert duplicate_email.status_code == 409
    assert duplicate_email.json()["error"]["code"] == "EMAIL_TAKEN"

    duplicate_handle = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"other-{uuid.uuid4().hex[:8]}@example.com",
            "password": "correct-password",
            "handle": handle,
            "name": "Other",
        },
    )
    assert duplicate_handle.status_code == 409
    assert duplicate_handle.json()["error"]["code"] == "HANDLE_TAKEN"

    client.cookies.clear()
    wrong = client.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password"})
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "INVALID_CREDENTIALS"
    missing = client.post(
        "/api/v1/auth/login",
        json={"email": "missing@example.com", "password": "wrong-password"},
    )
    assert missing.status_code == 401
    assert missing.json()["error"]["message"] == wrong.json()["error"]["message"]

    logged_in = client.post(
        "/api/v1/auth/login", json={"email": email, "password": "correct-password"}
    )
    assert logged_in.status_code == 200
    token = logged_in.json()["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == email
    assert "password_hash" not in me.text

    public = client.get(f"/api/v1/channels/{handle}")
    assert public.status_code == 200
    assert public.json()["name"] == "Studio"
    updated = client.patch(
        "/api/v1/channels/me",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Studio Two",
            "description": "Notes",
            "handle": "hacked",
            "user_id": str(uuid.uuid4()),
        },
    )
    assert updated.status_code == 200
    assert updated.json()["handle"] == handle
    assert updated.json()["name"] == "Studio Two"
    mine = client.get("/api/v1/channels/me", headers={"Authorization": f"Bearer {token}"})
    assert mine.json()["name"] == "Studio Two"


def test_refresh_rotation_reuse_and_logout(client: TestClient) -> None:
    email = f"rotate-{uuid.uuid4().hex[:8]}@example.com"
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "correct-password",
            "handle": f"h{uuid.uuid4().hex[:8]}",
            "name": "Rotate",
        },
    )
    first_refresh = registered.cookies["refresh_token"]
    rotated = client.post("/api/v1/auth/refresh")
    assert rotated.status_code == 200, rotated.text
    second_refresh = rotated.cookies["refresh_token"]
    assert second_refresh != first_refresh
    _use_refresh(client, first_refresh)
    reused = client.post("/api/v1/auth/refresh")
    assert reused.status_code == 401
    _use_refresh(client, second_refresh)
    family_rejected = client.post("/api/v1/auth/refresh")
    assert family_rejected.status_code == 401

    again = client.post("/api/v1/auth/login", json={"email": email, "password": "correct-password"})
    access = again.json()["access_token"]
    logged_out = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {access}"})
    assert logged_out.status_code == 204
    rejected = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert rejected.status_code == 401
    repeat = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {access}"})
    assert repeat.status_code == 401


def test_other_user_cannot_update_a_channel_and_inactive_user_is_rejected(
    client: TestClient,
) -> None:
    owner_email = f"owner-{uuid.uuid4().hex[:8]}@example.com"
    other_email = f"other-{uuid.uuid4().hex[:8]}@example.com"
    owner = client.post(
        "/api/v1/auth/register",
        json={
            "email": owner_email,
            "password": "correct-password",
            "handle": f"o{uuid.uuid4().hex[:8]}",
            "name": "Owner",
        },
    )
    other = client.post(
        "/api/v1/auth/register",
        json={
            "email": other_email,
            "password": "correct-password",
            "handle": f"x{uuid.uuid4().hex[:8]}",
            "name": "Other",
        },
    )
    owner_handle = owner.json()["user"]["channel"]["handle"]
    other_token = other.json()["access_token"]
    denied = client.patch(
        f"/api/v1/channels/{owner_handle}",
        headers={"Authorization": f"Bearer {other_token}"},
        json={"name": "Stolen"},
    )
    assert denied.status_code == 405
    own_update = client.patch(
        "/api/v1/channels/me",
        headers={"Authorization": f"Bearer {other_token}"},
        json={"name": "Other Name"},
    )
    assert own_update.status_code == 200
    assert own_update.json()["handle"] != owner_handle
    assert client.get(f"/api/v1/channels/{owner_handle}").json()["name"] == "Owner"

    session = SessionLocal()
    try:
        user = session.scalar(select(User).where(User.email == owner_email))
        assert user is not None
        deactivate_user(session, user)
    finally:
        session.close()
    inactive = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {owner.json()['access_token']}"},
    )
    assert inactive.status_code == 401


def test_user_cannot_call_an_admin_dependency(client: TestClient) -> None:
    _install_role_probes()
    email = f"role-{uuid.uuid4().hex[:8]}@example.com"
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "correct-password",
            "handle": f"r{uuid.uuid4().hex[:8]}",
            "name": "Role",
        },
    )
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    assert client.get("/api/v1/_test/admin").status_code == 401
    assert client.get("/api/v1/_test/admin", headers=headers).status_code == 403
    assert client.get("/api/v1/_test/moderator", headers=headers).status_code == 403

    session = SessionLocal()
    try:
        user = session.scalar(select(User).where(User.email == email))
        assert user is not None
        user.role = UserRole.ADMIN
        session.commit()
    finally:
        session.close()
    assert client.get("/api/v1/_test/admin", headers=headers).json()["role"] == "ADMIN"
    assert client.get("/api/v1/_test/moderator", headers=headers).status_code == 200
    session = SessionLocal()
    try:
        user = session.scalar(select(User).where(User.email == email))
        assert user is not None
        user.role = UserRole.USER
        session.commit()
    finally:
        session.close()


def test_bootstrap_does_not_reset_an_existing_admin() -> None:
    session = SessionLocal()
    try:
        ensure_bootstrap_admin(session)
        original = session.scalar(select(User.password_hash).where(User.role == UserRole.ADMIN))
        again = ensure_bootstrap_admin(session)
        current = session.scalar(select(User.password_hash).where(User.role == UserRole.ADMIN))
    finally:
        session.close()
    assert again == "exists"
    assert original is not None
    assert current == original


def test_identity_schema_downgrades(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    admin_url = make_url(settings.database_url).set(database="postgres")
    database_name = f"identity_{uuid.uuid4().hex[:12]}"
    maintenance = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maintenance.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    maintenance.dispose()

    empty_url = admin_url.set(database=database_name)
    monkeypatch.setenv("DATABASE_URL", empty_url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    engine = create_engine(empty_url)
    try:
        upgrade_head()
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT to_regclass('public.users')")).scalar() == "users"
            )
        cfg = Config("alembic.ini")
        command.downgrade(cfg, "0001_extensions")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT to_regclass('public.users')")).scalar() is None
            names = {row[0] for row in connection.execute(text("SELECT extname FROM pg_extension"))}
        assert "citext" in names
        command.upgrade(cfg, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT to_regclass('public.channels')")).scalar()
                == "channels"
            )
    finally:
        engine.dispose()
        get_settings.cache_clear()
        monkeypatch.undo()
        get_settings.cache_clear()
        cleanup = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with cleanup.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        cleanup.dispose()
