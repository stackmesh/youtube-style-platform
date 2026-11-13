"""Report queue, hides, and admin account actions."""

import uuid

from fastapi import APIRouter, Cookie, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.cookies import set_hls_cookie
from app.api.deps import get_current_user, require_admin, require_moderator
from app.api.playback import PlaybackResponse
from app.core.rate_limit import enforce_rate_limit
from app.db.models import ReportReason, ReportStatus, User, UserRole
from app.db.session import get_db
from app.identity.schemas import ErrorResponse
from app.moderation.service import (
    change_role,
    dismiss_report,
    hide_comment,
    hide_video,
    list_comment_reports,
    list_video_reports,
    moderator_playback,
    report_comment,
    report_video,
    restore_comment,
    restore_video,
    set_activation,
    video_history,
)
from app.playback.service import SESSION_TTL

router = APIRouter(tags=["moderation"])

_ERRORS = {
    401: {"model": ErrorResponse, "description": "Authentication is required."},
    403: {"model": ErrorResponse, "description": "The role does not allow this action."},
    404: {"model": ErrorResponse, "description": "The resource was not found."},
    409: {"model": ErrorResponse, "description": "The action conflicts with the current state."},
    429: {"model": ErrorResponse, "description": "Rate limit exceeded. Retry-After is set."},
}


class ReportCreate(BaseModel):
    reason: ReportReason
    details: str | None = Field(default=None, max_length=2000)


class ReasonBody(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    report_id: uuid.UUID | None = None


class RoleBody(BaseModel):
    role: UserRole
    reason: str = Field(min_length=1, max_length=2000)


class ActivationBody(BaseModel):
    active: bool
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/api/v1/videos/{video_id}/reports", status_code=204, responses=_ERRORS)
def create_video_report(
    video_id: uuid.UUID,
    body: ReportCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    enforce_rate_limit(f"rl:report:{user.id}", 10, 3600)
    report_video(session, user, video_id, body.reason, body.details)


@router.post("/api/v1/comments/{comment_id}/reports", status_code=204, responses=_ERRORS)
def create_comment_report(
    comment_id: uuid.UUID,
    body: ReportCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    enforce_rate_limit(f"rl:report:{user.id}", 10, 3600)
    report_comment(session, user, comment_id, body.reason, body.details)


@router.get("/api/v1/moderation/reports", responses=_ERRORS)
def video_reports(
    status: ReportStatus = ReportStatus.open,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    _moderator: User = Depends(require_moderator),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    return list_video_reports(session, status, cursor, limit)


@router.get("/api/v1/moderation/comment-reports", responses=_ERRORS)
def comment_reports(
    status: ReportStatus = ReportStatus.open,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    _moderator: User = Depends(require_moderator),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    return list_comment_reports(session, status, cursor, limit)


@router.post("/api/v1/moderation/reports/{report_id}/dismiss", status_code=204, responses=_ERRORS)
def dismiss(
    report_id: uuid.UUID,
    body: ReasonBody,
    moderator: User = Depends(require_moderator),
    session: Session = Depends(get_db),
) -> None:
    dismiss_report(session, moderator, report_id, body.reason)


@router.post("/api/v1/moderation/videos/{video_id}/hide", status_code=200, responses=_ERRORS)
def hide(
    video_id: uuid.UUID,
    body: ReasonBody,
    moderator: User = Depends(require_moderator),
    session: Session = Depends(get_db),
) -> Response:
    hide_video(session, moderator, video_id, body.reason, body.report_id)
    return Response(status_code=200)


@router.post("/api/v1/moderation/videos/{video_id}/restore", status_code=200, responses=_ERRORS)
def restore(
    video_id: uuid.UUID,
    body: ReasonBody,
    moderator: User = Depends(require_moderator),
    session: Session = Depends(get_db),
) -> Response:
    restore_video(session, moderator, video_id, body.reason)
    return Response(status_code=200)


@router.post("/api/v1/moderation/comments/{comment_id}/hide", status_code=204, responses=_ERRORS)
def hide_one_comment(
    comment_id: uuid.UUID,
    body: ReasonBody,
    moderator: User = Depends(require_moderator),
    session: Session = Depends(get_db),
) -> None:
    hide_comment(session, moderator, comment_id, body.reason)


@router.post("/api/v1/moderation/comments/{comment_id}/restore", status_code=204, responses=_ERRORS)
def restore_one_comment(
    comment_id: uuid.UUID,
    body: ReasonBody,
    moderator: User = Depends(require_moderator),
    session: Session = Depends(get_db),
) -> None:
    restore_comment(session, moderator, comment_id, body.reason)


@router.get("/api/v1/moderation/videos/{video_id}/history", responses=_ERRORS)
def history(
    video_id: uuid.UUID,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    _moderator: User = Depends(require_moderator),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    return video_history(session, video_id, cursor, limit)


@router.post(
    "/api/v1/moderation/videos/{video_id}/playback",
    response_model=PlaybackResponse,
    responses=_ERRORS,
)
def play(
    video_id: uuid.UUID,
    response: Response,
    moderator: User = Depends(require_moderator),
    session: Session = Depends(get_db),
    hls_session: str | None = Cookie(default=None),
) -> PlaybackResponse:
    opened = moderator_playback(session, moderator, video_id, hls_session)
    set_hls_cookie(response, video_id, str(opened.id), int(SESSION_TTL.total_seconds()))
    return PlaybackResponse(
        expires_at=opened.expires_at.isoformat(),
        playlist_url=f"/api/v1/videos/{video_id}/hls/master.m3u8",
    )


@router.post("/api/v1/admin/users/{user_id}/role", status_code=204, responses=_ERRORS)
def role(
    user_id: uuid.UUID,
    body: RoleBody,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> None:
    change_role(session, admin, user_id, body.role, body.reason)


@router.post("/api/v1/admin/users/{user_id}/deactivation", status_code=204, responses=_ERRORS)
def activation(
    user_id: uuid.UUID,
    body: ActivationBody,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> None:
    set_activation(session, admin, user_id, body.active, body.reason)
