from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.rate_limit import enforce_rate_limit
from app.db.models import User, UserRole
from app.db.session import get_db
from app.identity.tokens import TokenError, read_access_token

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise _unauthenticated()
    return _user_from_credentials(credentials, session)


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_db),
) -> User | None:
    """Anonymous when no token is sent. A presented token that fails is still 401."""
    if credentials is None or not credentials.credentials:
        return None
    if credentials.scheme.lower() != "bearer":
        raise _unauthenticated()
    return _user_from_credentials(credentials, session)


def _user_from_credentials(credentials: HTTPAuthorizationCredentials, session: Session) -> User:
    settings = get_settings()
    try:
        claims = read_access_token(credentials.credentials, settings.jwt_secret.get_secret_value())
    except TokenError:
        raise _unauthenticated() from None
    user = session.get(User, claims.user_id)
    if user is None or not user.is_active or user.session_epoch != claims.epoch:
        raise _unauthenticated()
    enforce_rate_limit(f"rl:user:{user.id}", 120, 60)
    return user


def assert_role(user: User, roles: tuple[UserRole, ...]) -> None:
    if user.role not in roles:
        raise AppError("FORBIDDEN", "You do not have permission.", 403)


def require_roles(*roles: UserRole):
    def dependency(user: User = Depends(get_current_user)) -> User:
        assert_role(user, roles)
        return user

    return dependency


require_moderator = require_roles(UserRole.MODERATOR, UserRole.ADMIN)
require_admin = require_roles(UserRole.ADMIN)


def _unauthenticated() -> AppError:
    return AppError("UNAUTHENTICATED", "Authentication is required.", 401)
