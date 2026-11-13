"""Playback sessions and private HLS routes. Segment bodies are not proxied."""

import uuid

from fastapi import APIRouter, Cookie, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.cookies import clear_hls_cookie, set_hls_cookie
from app.api.deps import get_optional_user
from app.api.network import client_ip
from app.core.exceptions import AppError
from app.core.rate_limit import enforce_rate_limit
from app.db.models import User
from app.db.session import get_db
from app.identity.schemas import ErrorResponse
from app.playback.service import (
    SESSION_TTL,
    end_playback,
    master_playlist,
    open_playback,
    public_thumbnail_key,
    record_view,
    segment_redirect,
    variant_playlist,
)
from app.uploads.objects import StorageError, presign_get

router = APIRouter(tags=["playback"])

_ERRORS = {
    404: {"model": ErrorResponse, "description": "Video not found."},
    409: {"model": ErrorResponse, "description": "Video is not ready."},
    429: {"model": ErrorResponse, "description": "Rate limit exceeded. Retry-After is set."},
    503: {"model": ErrorResponse, "description": "Playback is unavailable."},
}


class PlaybackResponse(BaseModel):
    expires_at: str
    playlist_url: str


@router.post(
    "/api/v1/videos/{video_id}/playback",
    response_model=PlaybackResponse,
    responses=_ERRORS,
)
def create_playback(
    video_id: uuid.UUID,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
    viewer: User | None = Depends(get_optional_user),
    hls_session: str | None = Cookie(default=None),
) -> PlaybackResponse:
    _limit_create(viewer, request)
    opened = open_playback(session, video_id, viewer, hls_session)
    set_hls_cookie(response, video_id, str(opened.id), int(SESSION_TTL.total_seconds()))
    return PlaybackResponse(
        expires_at=opened.expires_at.isoformat(),
        playlist_url=f"/api/v1/videos/{video_id}/hls/master.m3u8",
    )


@router.delete(
    "/api/v1/videos/{video_id}/playback",
    status_code=204,
    responses=_ERRORS,
)
def delete_playback(
    video_id: uuid.UUID,
    session: Session = Depends(get_db),
    viewer: User | None = Depends(get_optional_user),
    hls_session: str | None = Cookie(default=None),
) -> Response:
    end_playback(session, video_id, viewer, hls_session)
    result = Response(status_code=204)
    clear_hls_cookie(result, video_id)
    return result


@router.post("/api/v1/videos/{video_id}/views", status_code=204, responses=_ERRORS)
def create_view(
    video_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_db),
    viewer: User | None = Depends(get_optional_user),
    hls_session: str | None = Cookie(default=None),
) -> Response:
    _limit_session(hls_session)
    record_view(
        session,
        video_id,
        viewer,
        hls_session,
        client_ip(request),
        request.headers.get("user-agent", ""),
    )
    return Response(status_code=204)


@router.get("/api/v1/videos/{video_id}/hls/master.m3u8", responses=_ERRORS)
def read_master(
    video_id: uuid.UUID,
    session: Session = Depends(get_db),
    viewer: User | None = Depends(get_optional_user),
    hls_session: str | None = Cookie(default=None),
) -> Response:
    _limit_session(hls_session)
    body = master_playlist(session, video_id, viewer, hls_session)
    return _playlist(body)


@router.get("/api/v1/videos/{video_id}/hls/{rendition}/index.m3u8", responses=_ERRORS)
def read_variant(
    video_id: uuid.UUID,
    rendition: str,
    session: Session = Depends(get_db),
    viewer: User | None = Depends(get_optional_user),
    hls_session: str | None = Cookie(default=None),
) -> Response:
    _limit_session(hls_session)
    body = variant_playlist(session, video_id, rendition, viewer, hls_session)
    return _playlist(body)


@router.get("/api/v1/videos/{video_id}/hls/{rendition}/{segment}", responses=_ERRORS)
def read_segment(
    video_id: uuid.UUID,
    rendition: str,
    segment: str,
    session: Session = Depends(get_db),
    viewer: User | None = Depends(get_optional_user),
    hls_session: str | None = Cookie(default=None),
) -> RedirectResponse:
    _limit_session(hls_session)
    location = segment_redirect(session, video_id, rendition, segment, viewer, hls_session)
    return RedirectResponse(
        url=location,
        status_code=302,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/v1/videos/{video_id}/thumbnail", responses=_ERRORS)
def read_thumbnail(video_id: uuid.UUID, session: Session = Depends(get_db)) -> RedirectResponse:
    key = public_thumbnail_key(session, video_id)
    try:
        location = presign_get(key)
    except StorageError as exc:
        raise AppError("STORAGE_UNAVAILABLE", "Playback is unavailable.", 503) from exc
    return RedirectResponse(
        url=location,
        status_code=302,
        headers={"Cache-Control": "no-store"},
    )


def _playlist(body: str) -> Response:
    return Response(
        content=body,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store"},
    )


def _limit_create(viewer: User | None, request: Request) -> None:
    if viewer is not None:
        enforce_rate_limit(f"rl:playback:{viewer.id}", 30, 600)
    enforce_rate_limit(f"rl:playback-ip:{client_ip(request)}", 30, 600)


def _limit_session(cookie_value: str | None) -> None:
    if cookie_value:
        enforce_rate_limit(f"rl:hls:{cookie_value}", 600, 60)
