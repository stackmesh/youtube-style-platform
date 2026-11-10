from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.api.deps import assert_role
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.ids import uuid7
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
