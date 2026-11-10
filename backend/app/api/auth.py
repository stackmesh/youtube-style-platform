from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.cookies import REFRESH_COOKIE, clear_refresh_cookie, set_refresh_cookie
from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.exceptions import AppError, error_payload
from app.core.logging import request_id_var
from app.core.rate_limit import enforce_rate_limit
from app.db.models import User, UserStorageQuota
from app.db.session import get_db
from app.identity.passwords import normalize_email
from app.identity.schemas import (
    ErrorResponse,
    LoginRequest,
    MeResponse,
    QuotaSummary,
    RegisterRequest,
    SessionResponse,
)
from app.identity.service import (
    channel_for_user,
    login_account,
    logout_account,
    register_account,
    rotate_refresh_token,
)

router = APIRouter(prefix="/api/v1/auth", tags=["identity"])

_ERRORS = {
    401: {"model": ErrorResponse, "description": "Authentication failed."},
    409: {"model": ErrorResponse, "description": "Email or handle is already taken."},
    422: {"model": ErrorResponse, "description": "The request failed validation."},
    429: {"model": ErrorResponse, "description": "Rate limit exceeded. Retry-After is set."},
}


@router.post("/register", response_model=SessionResponse, responses=_ERRORS)
def register(
    body: RegisterRequest, request: Request, response: Response, session: Session = Depends(get_db)
) -> SessionResponse:
    """Create a user, channel, and quota. The role is always USER."""
    enforce_rate_limit(f"rl:register:{_client_ip(request)}", 5, 3600)
    user, access_token, refresh_token = register_account(
        session, body.email, body.password, body.handle, body.name
    )
    set_refresh_cookie(response, refresh_token)
    return _session(session, user, access_token)


@router.post("/login", response_model=SessionResponse, responses=_ERRORS)
def login(
    body: LoginRequest, request: Request, response: Response, session: Session = Depends(get_db)
) -> SessionResponse:
    """Check email and password. Failures use one message whether or not the account exists."""
    address = _client_ip(request)
    enforce_rate_limit(f"rl:login:ip:{address}", 10, 900)
    enforce_rate_limit(f"rl:login:email:{normalize_email(body.email)}", 10, 900)
    user, access_token, refresh_token = login_account(session, body.email, body.password)
    set_refresh_cookie(response, refresh_token)
    return _session(session, user, access_token)


@router.post("/refresh", response_model=SessionResponse, responses=_ERRORS)
def refresh(
    request: Request, response: Response, session: Session = Depends(get_db)
) -> SessionResponse | JSONResponse:
    """Rotate the refresh cookie and return a new access token. Reuse revokes the token family."""
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        return _refresh_failure(response)
    try:
        user, access_token, refresh_token = rotate_refresh_token(session, raw)
    except AppError as exc:
        failed = JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, request_id_var.get(), exc.details),
            headers=exc.headers,
        )
        clear_refresh_cookie(failed)
        return failed
    set_refresh_cookie(response, refresh_token)
    return _session(session, user, access_token)


@router.post("/logout", status_code=204, responses={401: _ERRORS[401]})
def logout(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    """Revoke the refresh family and bump session_epoch."""
    logout_account(session, user, request.cookies.get(REFRESH_COOKIE))
    clear_refresh_cookie(response)


@router.get("/me", response_model=MeResponse, responses={401: _ERRORS[401]})
def me(user: User = Depends(get_current_user), session: Session = Depends(get_db)) -> MeResponse:
    """Return the authenticated profile. Password hashes and refresh tokens are omitted."""
    channel = channel_for_user(session, user.id)
    quota = session.get(UserStorageQuota, user.id)
    if quota is None:
        raise AppError("CHANNEL_NOT_FOUND", "Channel not found.", 404)
    return MeResponse(
        id=user.id,
        email=user.email,
        role=user.role.value,
        channel=_channel(channel),
        quota=QuotaSummary(
            bytes_committed=quota.bytes_committed,
            bytes_reserved=quota.bytes_reserved,
            bytes_limit=quota.bytes_limit,
        ),
    )


def _session(session: Session, user: User, access_token: str) -> SessionResponse:
    channel = channel_for_user(session, user.id)
    settings = get_settings()
    return SessionResponse(
        access_token=access_token,
        expires_in=settings.access_token_ttl_seconds,
        user={
            "id": user.id,
            "email": user.email,
            "role": user.role.value,
            "channel": _channel(channel),
        },
    )


def _channel(channel) -> dict[str, object]:
    return {
        "handle": channel.handle,
        "name": channel.name,
        "description": channel.description,
        "avatar_url": None,
        "subscriber_count": channel.subscriber_count,
        "viewer_subscribed": False,
    }


def _refresh_failure(response: Response) -> JSONResponse:
    failed = JSONResponse(
        status_code=401,
        content=error_payload(
            "UNAUTHENTICATED", "Authentication is required.", request_id_var.get()
        ),
    )
    clear_refresh_cookie(failed)
    clear_refresh_cookie(response)
    return failed


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip() or "unknown"
    if request.client is None:
        return "unknown"
    return request.client.host
