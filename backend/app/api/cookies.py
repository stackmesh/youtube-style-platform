from fastapi import Response

from app.core.config import get_settings

REFRESH_COOKIE = "refresh_token"
REFRESH_PATH = "/api/v1/auth"


def set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        max_age=settings.refresh_token_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=REFRESH_PATH,
    )


def clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=REFRESH_COOKIE,
        path=REFRESH_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
