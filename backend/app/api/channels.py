from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_optional_user
from app.api.videos import _summary_page
from app.catalogue.schemas import VideoPageResponse
from app.catalogue.service import list_channel_videos
from app.db.models import User
from app.db.session import get_db
from app.identity.schemas import ChannelSummary, ChannelUpdateRequest, ErrorResponse
from app.identity.service import channel_for_user, public_channel, update_own_channel

router = APIRouter(prefix="/api/v1/channels", tags=["channels"])

_ERRORS = {
    401: {"model": ErrorResponse, "description": "Authentication is required."},
    404: {"model": ErrorResponse, "description": "The channel does not exist."},
    422: {"model": ErrorResponse, "description": "The request failed validation."},
}


@router.get("/me", response_model=ChannelSummary, responses={401: _ERRORS[401], 404: _ERRORS[404]})
def my_channel(
    user: User = Depends(get_current_user), session: Session = Depends(get_db)
) -> ChannelSummary:
    """Return the authenticated user's channel. Ownership cannot be changed through this API."""
    return _summary(channel_for_user(session, user.id))


@router.patch("/me", response_model=ChannelSummary, responses=_ERRORS)
def update_my_channel(
    body: ChannelUpdateRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> ChannelSummary:
    """Update the caller's name and description. Handle and owner stay fixed."""
    channel = update_own_channel(session, user, body.name, body.description)
    return _summary(channel)


@router.get(
    "/{handle}/videos",
    response_model=VideoPageResponse,
    response_model_exclude_none=True,
    responses={404: _ERRORS[404]},
)
def channel_videos(
    handle: str,
    limit: int = Query(default=24, ge=1, le=50),
    cursor: str | None = None,
    viewer: User | None = Depends(get_optional_user),
    session: Session = Depends(get_db),
) -> VideoPageResponse:
    """Public ready videos on a channel. The owner also sees drafts and other non-deleted states."""
    return _summary_page(list_channel_videos(session, handle, viewer, cursor, limit))


@router.get("/{handle}", response_model=ChannelSummary, responses={404: _ERRORS[404]})
def read_channel(handle: str, session: Session = Depends(get_db)) -> ChannelSummary:
    """Return a public channel. Authentication is not required."""
    return _summary(public_channel(session, handle))


def _summary(channel) -> ChannelSummary:
    return ChannelSummary(
        handle=channel.handle,
        name=channel.name,
        description=channel.description,
        avatar_url=None,
        subscriber_count=channel.subscriber_count,
        viewer_subscribed=False,
    )
