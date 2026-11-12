import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.ids import uuid7
from app.core.rate_limit import enforce_rate_limit
from app.db.models import Channel, RefreshToken, User, UserRole, UserStorageQuota
from app.identity.passwords import (
    email_is_valid,
    handle_is_valid,
    hash_password,
    normalize_email,
    normalize_handle,
    password_is_valid,
    verify_dummy_password,
    verify_password,
)
from app.identity.tokens import hash_refresh_token, issue_access_token, new_refresh_token

logger = logging.getLogger(__name__)
_REJECTED_BOOTSTRAP_PASSWORD = "unused-until-stage-2"


def register_account(
    session: Session, email: str, password: str, handle: str, name: str
) -> tuple[User, str, str]:
    normalized_email = _require_email(email)
    normalized_handle = _require_handle(handle)
    _require_password(password)
    display_name = _require_name(name)
    if _email_exists(session, normalized_email):
        raise AppError("EMAIL_TAKEN", "An account with that email already exists.", 409)
    if _handle_exists(session, normalized_handle):
        raise AppError("HANDLE_TAKEN", "That handle is already taken.", 409)

    settings = get_settings()
    user = User(
        id=uuid7(),
        email=normalized_email,
        password_hash=hash_password(password),
        role=UserRole.USER,
        is_active=True,
        session_epoch=1,
    )
    channel = Channel(
        id=uuid7(), user_id=user.id, handle=normalized_handle, name=display_name, description=""
    )
    quota = UserStorageQuota(user_id=user.id, bytes_limit=settings.default_quota_bytes)
    session.add(user)
    session.add(channel)
    session.add(quota)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise _conflict_from_integrity(exc) from exc
    access_token, refresh_token = _start_session(session, user)
    session.commit()
    return user, access_token, refresh_token


def login_account(session: Session, email: str, password: str) -> tuple[User, str, str]:
    normalized = normalize_email(email)
    user = session.scalar(select(User).where(User.email == normalized))
    if user is None:
        verify_dummy_password(password)
        raise _invalid_credentials()
    if not verify_password(password, user.password_hash) or not user.is_active:
        raise _invalid_credentials()
    access_token, refresh_token = _start_session(session, user)
    session.commit()
    return user, access_token, refresh_token


def rotate_refresh_token(session: Session, raw_token: str) -> tuple[User, str, str]:
    now = datetime.now(UTC)
    token_hash = hash_refresh_token(raw_token)
    row = session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    )
    if row is None:
        raise _unauthenticated()
    user = session.get(User, row.user_id)
    if user is None or not user.is_active:
        raise _unauthenticated()
    enforce_rate_limit(f"rl:refresh:{user.id}", 60, 3600)
    if row.revoked_at is not None:
        _revoke_family(session, row.family_id, now)
        session.commit()
        logger.info("refresh token reuse revoked the token family")
        raise _unauthenticated()
    if row.expires_at <= now:
        raise _unauthenticated()
    access_token, refresh_token, new_row = _issue_pair(user, row.family_id)
    session.add(new_row)
    session.flush()
    row.revoked_at = now
    row.replaced_by_id = new_row.id
    session.commit()
    return user, access_token, refresh_token


def logout_account(session: Session, user: User, raw_token: str | None) -> None:
    now = datetime.now(UTC)
    if raw_token:
        token_hash = hash_refresh_token(raw_token)
        row = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        if row is not None and row.user_id == user.id:
            _revoke_family(session, row.family_id, now)
        else:
            _revoke_user_tokens(session, user.id, now)
    else:
        _revoke_user_tokens(session, user.id, now)
    user.session_epoch += 1
    user.updated_at = now
    session.commit()


def deactivate_user(session: Session, user: User) -> None:
    """Stop an account. There is no public route for this in stage 2."""
    now = datetime.now(UTC)
    user.is_active = False
    user.session_epoch += 1
    user.updated_at = now
    _revoke_user_tokens(session, user.id, now)
    session.commit()


def ensure_bootstrap_admin(session: Session) -> str:
    settings = get_settings()
    existing = session.scalar(select(User.id).where(User.role == UserRole.ADMIN).limit(1))
    if existing is not None:
        logger.info("bootstrap skipped because an admin already exists")
        return "exists"
    email = normalize_email(settings.bootstrap_admin_email)
    password = settings.bootstrap_admin_password.get_secret_value()
    if (
        password == _REJECTED_BOOTSTRAP_PASSWORD
        or not email_is_valid(email)
        or not password_is_valid(password)
    ):
        raise SystemExit("bootstrap admin email or password does not meet the account rules")
    handle = _admin_handle(email)
    if _handle_exists(session, handle):
        raise SystemExit("bootstrap admin handle is already in use")
    user = User(
        id=uuid7(),
        email=email,
        password_hash=hash_password(password),
        role=UserRole.ADMIN,
        is_active=True,
        session_epoch=1,
    )
    session.add(user)
    session.add(Channel(id=uuid7(), user_id=user.id, handle=handle, name="Admin", description=""))
    session.add(UserStorageQuota(user_id=user.id, bytes_limit=settings.default_quota_bytes))
    session.commit()
    logger.info("bootstrap admin created")
    return "created"


def channel_for_user(session: Session, user_id: uuid.UUID) -> Channel:
    channel = session.scalar(select(Channel).where(Channel.user_id == user_id))
    if channel is None:
        raise AppError("CHANNEL_NOT_FOUND", "Channel not found.", 404)
    return channel


def public_channel(session: Session, handle: str) -> Channel:
    normalized = normalize_handle(handle)
    channel = session.scalar(select(Channel).where(Channel.handle == normalized))
    if channel is None:
        raise AppError("CHANNEL_NOT_FOUND", "Channel not found.", 404)
    return channel


def update_own_channel(
    session: Session, user: User, name: str | None, description: str | None
) -> Channel:
    channel = channel_for_user(session, user.id)
    if name is not None:
        channel.name = _require_name(name)
    if description is not None:
        channel.description = _require_description(description)
    channel.updated_at = datetime.now(UTC)
    session.commit()
    session.refresh(channel)
    return channel


def _start_session(session: Session, user: User) -> tuple[str, str]:
    access_token, refresh_token, row = _issue_pair(user)
    session.add(row)
    return access_token, refresh_token


def _issue_pair(user: User, family_id: uuid.UUID | None = None) -> tuple[str, str, RefreshToken]:
    settings = get_settings()
    now = datetime.now(UTC)
    access_token, _ttl = issue_access_token(
        user_id=user.id,
        epoch=user.session_epoch,
        secret=settings.jwt_secret.get_secret_value(),
        ttl_seconds=settings.access_token_ttl_seconds,
        now=now,
    )
    refresh_token = new_refresh_token()
    row = RefreshToken(
        id=uuid7(),
        user_id=user.id,
        token_hash=hash_refresh_token(refresh_token),
        family_id=family_id or uuid7(),
        expires_at=now + timedelta(seconds=settings.refresh_token_ttl_seconds),
    )
    return access_token, refresh_token, row


def _revoke_family(session: Session, family_id: uuid.UUID, now: datetime) -> None:
    session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


def _revoke_user_tokens(session: Session, user_id: uuid.UUID, now: datetime) -> None:
    session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


def _email_exists(session: Session, email: str) -> bool:
    return session.scalar(select(User.id).where(User.email == email)) is not None


def _handle_exists(session: Session, handle: str) -> bool:
    return session.scalar(select(Channel.id).where(Channel.handle == handle)) is not None


def _require_email(email: str) -> str:
    normalized = normalize_email(email)
    if not email_is_valid(normalized):
        raise AppError(
            "VALIDATION_ERROR",
            "Enter a valid email address.",
            422,
            [{"field": "email", "issue": "invalid"}],
        )
    return normalized


def _require_handle(handle: str) -> str:
    normalized = normalize_handle(handle)
    if not handle_is_valid(normalized):
        raise AppError(
            "VALIDATION_ERROR",
            "Handle must be 3 to 30 characters of lowercase letters, digits, or underscores.",
            422,
            [{"field": "handle", "issue": "invalid"}],
        )
    return normalized


def _require_password(password: str) -> None:
    if not password_is_valid(password):
        raise AppError(
            "VALIDATION_ERROR",
            "Password must be 10 to 128 characters.",
            422,
            [{"field": "password", "issue": "invalid_length"}],
        )


def _require_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > 80:
        raise AppError(
            "VALIDATION_ERROR",
            "Name must be 1 to 80 characters.",
            422,
            [{"field": "name", "issue": "invalid_length"}],
        )
    return cleaned


def _require_description(description: str) -> str:
    if len(description) > 2000:
        raise AppError(
            "VALIDATION_ERROR",
            "Description must be at most 2000 characters.",
            422,
            [{"field": "description", "issue": "too_long"}],
        )
    return description


def _admin_handle(email: str) -> str:
    local = email.split("@", 1)[0]
    cleaned = "".join(character if character.isalnum() else "_" for character in local).strip("_")
    if len(cleaned) < 3:
        cleaned = "admin"
    return cleaned[:30]


def _invalid_credentials() -> AppError:
    return AppError("INVALID_CREDENTIALS", "Email or password is incorrect.", 401)


def _unauthenticated() -> AppError:
    return AppError("UNAUTHENTICATED", "Authentication is required.", 401)


def _conflict_from_integrity(exc: IntegrityError) -> AppError:
    constraint = str(getattr(getattr(exc.orig, "diag", None), "constraint_name", "") or "")
    if "handle" in constraint:
        return AppError("HANDLE_TAKEN", "That handle is already taken.", 409)
    return AppError("EMAIL_TAKEN", "An account with that email already exists.", 409)
