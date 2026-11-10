from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
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
