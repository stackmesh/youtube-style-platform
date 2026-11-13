"""Reactions, comments, and subscriptions."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_optional_user
from app.api.videos import _summary_page
from app.catalogue.schemas import VideoPageResponse
from app.core.rate_limit import enforce_rate_limit
from app.db.models import User
from app.db.session import get_db
from app.identity.schemas import ChannelSummary, ErrorResponse
from app.social.service import (
    add_comment,
    delete_comment,
    edit_comment,
    list_comments,
    list_replies,
    list_subscribers,
    list_subscriptions,
    set_reaction,
    subscribe,
    subscription_feed,
    unsubscribe,
)

router = APIRouter(tags=["engagement"])

_ERRORS = {
    401: {"model": ErrorResponse, "description": "Authentication is required."},
    403: {"model": ErrorResponse, "description": "The caller cannot change this comment."},
    404: {"model": ErrorResponse, "description": "The resource was not found."},
    409: {"model": ErrorResponse, "description": "The action conflicts with the current state."},
    429: {"model": ErrorResponse, "description": "Rate limit exceeded. Retry-After is set."},
}


class ReactionRequest(BaseModel):
    value: str = Field(pattern="^(like|dislike|none)$")


class ReactionResponse(BaseModel):
    like_count: int
    dislike_count: int
    viewer_reaction: str | None


class CommentAuthor(BaseModel):
    handle: str
    name: str


class CommentItem(BaseModel):
    id: uuid.UUID
    body: str
    created_at: datetime
    edited: bool
    deleted: bool
    author: CommentAuthor | None
    reply_count: int
    replies: list["CommentItem"] = Field(default_factory=list)


CommentItem.model_rebuild()


class CommentPage(BaseModel):
    items: list[CommentItem]
    next_cursor: str | None


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    parent_id: uuid.UUID | None = None


class CommentEdit(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


@router.put(
    "/api/v1/videos/{video_id}/reaction",
    response_model=ReactionResponse,
    responses=_ERRORS,
)
def put_reaction(
    video_id: uuid.UUID,
    body: ReactionRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> ReactionResponse:
    enforce_rate_limit(f"rl:reaction:{user.id}", 60, 600)
    result = set_reaction(session, user, video_id, body.value)
    return ReactionResponse(
        like_count=int(result["like_count"]),
        dislike_count=int(result["dislike_count"]),
        viewer_reaction=result["viewer_reaction"]
        if isinstance(result["viewer_reaction"], str)
        else None,
    )


@router.get(
    "/api/v1/videos/{video_id}/comments",
    response_model=CommentPage,
    responses={404: _ERRORS[404]},
)
def comments(
    video_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
    viewer: User | None = Depends(get_optional_user),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    return list_comments(session, video_id, viewer, cursor, limit)


@router.get("/api/v1/comments/{comment_id}/replies", response_model=CommentPage, responses=_ERRORS)
def replies(
    comment_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
    viewer: User | None = Depends(get_optional_user),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    return list_replies(session, comment_id, viewer, cursor, limit)


@router.post(
    "/api/v1/videos/{video_id}/comments",
    response_model=CommentItem,
    responses=_ERRORS,
)
def create_comment(
    video_id: uuid.UUID,
    body: CommentCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    enforce_rate_limit(f"rl:comment:{user.id}", 20, 600)
    return add_comment(session, user, video_id, body.body, body.parent_id)


@router.patch("/api/v1/comments/{comment_id}", response_model=CommentItem, responses=_ERRORS)
def patch_comment(
    comment_id: uuid.UUID,
    body: CommentEdit,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    return edit_comment(session, user, comment_id, body.body)


@router.delete("/api/v1/comments/{comment_id}", status_code=204, responses=_ERRORS)
def remove_comment(
    comment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    delete_comment(session, user, comment_id)


@router.put("/api/v1/channels/{handle}/subscription", status_code=204, responses=_ERRORS)
def put_subscription(
    handle: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    enforce_rate_limit(f"rl:subscription:{user.id}", 60, 600)
    subscribe(session, user, handle)


@router.delete("/api/v1/channels/{handle}/subscription", status_code=204, responses=_ERRORS)
def delete_subscription(
    handle: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    enforce_rate_limit(f"rl:subscription:{user.id}", 60, 600)
    unsubscribe(session, user, handle)


@router.get("/api/v1/me/subscriptions", response_model=list[ChannelSummary], responses=_ERRORS)
def my_subscriptions(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[ChannelSummary]:
    return [_channel(channel, True) for channel in list_subscriptions(session, user)]


@router.get(
    "/api/v1/me/subscriptions/videos",
    response_model=VideoPageResponse,
    response_model_exclude_none=True,
    responses=_ERRORS,
)
def subscription_videos(
    limit: int = Query(default=24, ge=1, le=50),
    cursor: str | None = None,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> VideoPageResponse:
    return _summary_page(subscription_feed(session, user, cursor, limit))


@router.get(
    "/api/v1/channels/{handle}/subscribers",
    response_model=list[ChannelSummary],
    responses=_ERRORS,
)
def subscribers(
    handle: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[ChannelSummary]:
    return [_channel(channel, False) for channel in list_subscribers(session, user, handle)]


def _channel(channel, subscribed: bool) -> ChannelSummary:
    return ChannelSummary(
        handle=channel.handle,
        name=channel.name,
        description=channel.description,
        avatar_url=None,
        subscriber_count=channel.subscriber_count,
        viewer_subscribed=subscribed,
    )
