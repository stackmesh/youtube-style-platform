import base64
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from redis.exceptions import RedisError

from app.api.deps import assert_role
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.ids import uuid7
from app.core.rate_limit import enforce_rate_limit
from app.db.models import User, UserRole
from app.identity.passwords import (
    email_is_valid,
    handle_is_valid,
    hash_password,
    normalize_email,
    normalize_handle,
    password_is_valid,
    verify_password,
)
from app.identity.service import ensure_bootstrap_admin
from app.identity.tokens import (
    TokenError,
    hash_refresh_token,
    issue_access_token,
    new_refresh_token,
    read_access_token,
)


def test_email_is_normalized_before_lookup() -> None:
    assert normalize_email("  Person@Example.COM ") == "person@example.com"
    assert email_is_valid("person@example.com") is True
    assert email_is_valid("not-an-email") is False


def test_password_policy_and_hash() -> None:
    assert password_is_valid("short") is False
    assert password_is_valid("correct-password") is True
    stored = hash_password("correct-password")
    assert stored != "correct-password"
    assert stored.startswith("$argon2id$")
    assert verify_password("correct-password", stored) is True
    assert verify_password("wrong-password", stored) is False


def test_handle_rules() -> None:
    assert normalize_handle("Studio_1") == "studio_1"
    assert handle_is_valid("Studio_1") is True
    assert handle_is_valid("no") is False
    assert handle_is_valid("has-dash") is False


def test_refresh_token_is_stored_as_a_hash() -> None:
    raw = new_refresh_token()
    digest = hash_refresh_token(raw)
    assert digest != raw.encode()
    assert len(digest) == 32


def test_access_token_round_trip_and_expiry() -> None:
    settings = get_settings()
    secret = settings.jwt_secret.get_secret_value()
    user_id = uuid7()
    token, ttl = issue_access_token(
        user_id=user_id,
        epoch=1,
        secret=secret,
        ttl_seconds=settings.access_token_ttl_seconds,
    )
    assert ttl == settings.access_token_ttl_seconds
    claims = read_access_token(token, secret)
    assert claims.user_id == user_id
    assert claims.epoch == 1
    expired = jwt.encode(
        {
            "sub": str(user_id),
            "iat": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
            "exp": int((datetime.now(UTC) - timedelta(minutes=20)).timestamp()),
            "jti": str(uuid7()),
            "epoch": 1,
        },
        secret,
        algorithm="HS256",
    )
    with pytest.raises(TokenError):
        read_access_token(expired, secret)


def test_rejects_tampered_and_unsigned_access_tokens() -> None:
    settings = get_settings()
    secret = settings.jwt_secret.get_secret_value()
    user_id = uuid7()
    token, _ttl = issue_access_token(
        user_id=user_id,
        epoch=1,
        secret=secret,
        ttl_seconds=settings.access_token_ttl_seconds,
    )
    tampered = token[:-1] + ("a" if not token.endswith("a") else "b")
    with pytest.raises(TokenError):
        read_access_token(tampered, secret)
    with pytest.raises(TokenError):
        read_access_token("not-a-jwt", secret)
    forged = jwt.encode(
        {
            "sub": str(user_id),
            "iat": 1,
            "exp": 4_102_444_800,
            "jti": str(uuid7()),
            "epoch": 1,
        },
        "another-secret-that-is-long-enough",
        algorithm="HS256",
    )
    with pytest.raises(TokenError):
        read_access_token(forged, secret)
    unsigned = _unsigned_jwt(
        {"sub": str(user_id), "iat": 1, "exp": 4_102_444_800, "jti": str(uuid7()), "epoch": 1}
    )
    with pytest.raises(TokenError):
        read_access_token(unsigned, secret)


def _unsigned_jwt(payload: dict[str, object]) -> str:
    def encode(value: dict[str, object]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{encode({'alg': 'none', 'typ': 'JWT'})}.{encode(payload)}."


class _Limiter:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def pipeline(self) -> "_Pipe":
        return _Pipe(self)


class _Pipe:
    def __init__(self, parent: _Limiter) -> None:
        self.parent = parent
        self.key = ""

    def zremrangebyscore(self, key: str, _low: float, _high: float) -> None:
        self.key = key

    def zadd(self, key: str, _mapping: dict[str, float]) -> None:
        self.key = key
        self.parent.counts[key] = self.parent.counts.get(key, 0) + 1

    def zcard(self, key: str) -> None:
        self.key = key

    def expire(self, key: str, _seconds: int) -> None:
        self.key = key

    def execute(self) -> tuple[int, int, int, int]:
        return (0, 0, self.parent.counts[self.key], 1)


class _DownRedis:
    def pipeline(self) -> object:
        raise RedisError("down")


def test_rate_limit_blocks_after_the_allowance(monkeypatch: pytest.MonkeyPatch) -> None:
    limiter = _Limiter()
    monkeypatch.setattr("app.core.rate_limit.redis_client", lambda: limiter)
    enforce_rate_limit("rl:register:203.0.113.5", 2, 3600)
    enforce_rate_limit("rl:register:203.0.113.5", 2, 3600)
    with pytest.raises(AppError) as caught:
        enforce_rate_limit("rl:register:203.0.113.5", 2, 3600)
    assert caught.value.status_code == 429
    assert caught.value.code == "RATE_LIMITED"
    assert caught.value.headers["Retry-After"] == "3600"


def test_rate_limit_fails_closed_when_redis_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.rate_limit.redis_client", lambda: _DownRedis())
    with pytest.raises(AppError) as caught:
        enforce_rate_limit("rl:login:ip:203.0.113.5", 10, 900)
    assert caught.value.status_code == 503


def test_bootstrap_rejects_the_placeholder_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "unused-until-stage-2")
    get_settings.cache_clear()

    class _EmptyAdmin:
        def scalar(self, _statement: object) -> None:
            return None

    try:
        with pytest.raises(SystemExit):
            ensure_bootstrap_admin(_EmptyAdmin())  # type: ignore[arg-type]
    finally:
        get_settings.cache_clear()


def test_role_dependency_rejects_a_user() -> None:
    user = User(
        id=uuid7(),
        email="person@example.com",
        password_hash="hash",
        role=UserRole.USER,
        is_active=True,
        session_epoch=1,
    )
    with pytest.raises(AppError) as caught:
        assert_role(user, (UserRole.MODERATOR, UserRole.ADMIN))
    assert caught.value.status_code == 403
    assert_role(user, (UserRole.USER,))
