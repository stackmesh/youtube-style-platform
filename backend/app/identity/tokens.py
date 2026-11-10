import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt


class TokenError(Exception):
    """The access token is missing, malformed, or expired."""


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    epoch: int
    jti: str
    expires_at: datetime


def new_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


def issue_access_token(
    *,
    user_id: uuid.UUID,
    epoch: int,
    secret: str,
    ttl_seconds: int,
    now: datetime | None = None,
) -> tuple[str, int]:
    issued = now or datetime.now(UTC)
    expires = issued + timedelta(seconds=ttl_seconds)
    payload = {
        "sub": str(user_id),
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": str(uuid.uuid4()),
        "epoch": epoch,
    }
    encoded = jwt.encode(payload, secret, algorithm="HS256")
    return encoded, ttl_seconds


def read_access_token(token: str, secret: str, *, now: datetime | None = None) -> AccessClaims:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            leeway=30,
            options={"require": ["sub", "iat", "exp", "jti", "epoch"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError from exc
    try:
        user_id = uuid.UUID(str(payload["sub"]))
        epoch = payload["epoch"]
        if isinstance(epoch, bool) or not isinstance(epoch, int):
            raise TokenError
        jti = str(payload["jti"])
        expires_at = datetime.fromtimestamp(int(payload["exp"]), UTC)
    except (TypeError, ValueError) as exc:
        raise TokenError from exc
    if now is not None and expires_at + timedelta(seconds=30) < now:
        raise TokenError
    return AccessClaims(user_id=user_id, epoch=epoch, jti=jti, expires_at=expires_at)
