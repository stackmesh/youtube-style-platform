import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_optional_user
from app.api.network import client_ip
from app.catalogue.schemas import (
    GrantCreateRequest,
    GrantListResponse,
    GrantResponse,
    VideoCreateRequest,
    VideoDetail,
    VideoPageResponse,
    VideoSummary,
    VideoUpdateRequest,
)
from app.catalogue.service import (
    VideoCard,
    VideoPage,
    create_video,
    delete_video,
    grant_access,
    list_feed,
    list_grants,
    list_owned,
    list_related,
    read_video,
    revoke_grant,
    update_video,
)
from app.core.rate_limit import enforce_rate_limit
from app.db.models import User
from app.db.session import get_db
from app.identity.schemas import ErrorResponse

router = APIRouter(tags=["catalogue"])

_ERRORS = {
    401: {"model": ErrorResponse, "description": "Authentication is required."},
    404: {"model": ErrorResponse, "description": "Video not found."},
    409: {"model": ErrorResponse, "description": "The grant conflicts with an existing grant."},
    422: {"model": ErrorResponse, "description": "The request failed validation."},
    429: {"model": ErrorResponse, "description": "Rate limit exceeded. Retry-After is set."},
}


@router.get("/api/v1/videos", response_model=VideoPageResponse, response_model_exclude_none=True)
def feed(
    limit: int = Query(default=24, ge=1, le=50),
    cursor: str | None = None,
    session: Session = Depends(get_db),
) -> VideoPageResponse:
    """Public ready videos, newest first. Drafts, unlisted, private, and hidden rows are absent."""
    return _summary_page(list_feed(session, cursor, limit))


@router.post(
    "/api/v1/videos",
    response_model=VideoDetail,
    response_model_exclude_none=True,
    responses=_ERRORS,
)
def create(
    body: VideoCreateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> VideoDetail:
    """Create a draft on the caller's channel. The video stays unpublished until processing."""
    enforce_rate_limit(f"rl:videos:ip:{client_ip(request)}", 30, 3600)
    enforce_rate_limit(f"rl:videos:user:{user.id}", 10, 3600)
    return _detail(
        create_video(session, user, body.title, body.description, body.tags, body.visibility),
        session,
        user,
    )


@router.get(
    "/api/v1/me/videos",
    response_model=VideoPageResponse,
    response_model_exclude_none=True,
    responses={401: _ERRORS[401]},
)
def my_videos(
    limit: int = Query(default=24, ge=1, le=50),
    cursor: str | None = None,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> VideoPageResponse:
    """The caller's non-deleted videos, including drafts, newest created first."""
    return _summary_page(list_owned(session, user, cursor, limit))


@router.get(
    "/api/v1/videos/{video_id}",
    response_model=VideoDetail,
    response_model_exclude_none=True,
    responses={404: _ERRORS[404]},
)
def detail(
    video_id: uuid.UUID,
    viewer: User | None = Depends(get_optional_user),
    session: Session = Depends(get_db),
) -> VideoDetail:
    """Video metadata. Callers who fail the ACL receive 404."""
    return _detail(read_video(session, video_id, viewer), session, viewer)


@router.patch(
    "/api/v1/videos/{video_id}",
    response_model=VideoDetail,
    response_model_exclude_none=True,
    responses=_ERRORS,
)
def update(
    video_id: uuid.UUID,
    body: VideoUpdateRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> VideoDetail:
    """Update the owner's metadata. A visibility change bumps acl_version and keeps grants."""
    return _detail(
        update_video(
            session,
            user,
            video_id,
            title=body.title,
            description=body.description,
            tags=body.tags,
            visibility=body.visibility,
        ),
        session,
        user,
    )


@router.delete(
    "/api/v1/videos/{video_id}",
    status_code=204,
    responses={401: _ERRORS[401], 404: _ERRORS[404]},
)
def remove(
    video_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> Response:
    """Soft-delete the owner's video."""
    delete_video(session, user, video_id)
    return Response(status_code=204)


@router.get(
    "/api/v1/videos/{video_id}/related",
    response_model=VideoPageResponse,
    response_model_exclude_none=True,
    responses={404: _ERRORS[404]},
)
def related(
    video_id: uuid.UUID,
    viewer: User | None = Depends(get_optional_user),
    session: Session = Depends(get_db),
) -> VideoPageResponse:
    """Up to 20 public ready videos. Shared tags come before other videos from the same channel."""
    cards = list_related(session, video_id, viewer)
    return VideoPageResponse(items=[_summary(card) for card in cards], next_cursor=None)


@router.get(
    "/api/v1/videos/{video_id}/grants",
    response_model=GrantListResponse,
    responses=_ERRORS,
)
def grants(
    video_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> GrantListResponse:
    """Active and revoked grants. Handles and timestamps only."""
    return GrantListResponse(
        items=[
            GrantResponse(
                handle=item.handle, created_at=item.created_at, revoked_at=item.revoked_at
            )
            for item in list_grants(session, user, video_id)
        ]
    )


@router.post(
    "/api/v1/videos/{video_id}/grants",
    response_model=GrantResponse,
    responses=_ERRORS,
)
def add_grant(
    video_id: uuid.UUID,
    body: GrantCreateRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> GrantResponse:
    """Grant a channel access to this video. The grantee is a handle, not a user id."""
    grant = grant_access(session, user, video_id, body.handle)
    return GrantResponse(
        handle=grant.handle, created_at=grant.created_at, revoked_at=grant.revoked_at
    )


@router.delete(
    "/api/v1/videos/{video_id}/grants/{handle}",
    status_code=204,
    responses=_ERRORS,
)
def remove_grant(
    video_id: uuid.UUID,
    handle: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> Response:
    """Revoke the active grant for a handle and bump acl_version."""
    revoke_grant(session, user, video_id, handle)
    return Response(status_code=204)


def _summary_page(page: VideoPage) -> VideoPageResponse:
    return VideoPageResponse(
        items=[_summary(card) for card in page.items], next_cursor=page.next_cursor
    )


def _summary(card: VideoCard) -> VideoSummary:
    payload = VideoSummary(
        id=card.video.id,
        title=card.video.title,
        thumbnail_url=None,
        channel={"handle": card.channel.handle, "name": card.channel.name},
        view_count=card.video.view_count,
        published_at=card.video.published_at,
        duration_seconds=card.video.duration_seconds,
    )
    if card.include_status:
        payload.visibility = card.video.visibility
        payload.processing_status = card.video.processing_status
        payload.moderation_status = card.video.moderation_status
    return payload


def _detail(
    card: VideoCard, session: Session | None = None, viewer: User | None = None
) -> VideoDetail:
    reaction = None
    subscribed = False
    if session is not None and viewer is not None:
        from app.social.service import viewer_flags

        reaction, subscribed = viewer_flags(session, viewer, card.video, card.channel)
    summary = _summary(card)
    return VideoDetail(
        **summary.model_dump(),
        description=card.video.description,
        tags=list(card.tags),
        like_count=card.video.like_count,
        dislike_count=card.video.dislike_count,
        comment_count=card.video.comment_count,
        viewer_reaction=reaction,
        viewer_subscribed=subscribed,
    )
