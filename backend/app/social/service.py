"""Reactions, comments, and subscriptions. Counts move in the same transaction as the row."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from app.catalogue.acl import public_discovery_filters, require_visible_video, video_not_found
from app.catalogue.cursors import decode_time_id, encode_time_id
from app.catalogue.service import VideoPage, _page_from_rows
from app.core.exceptions import AppError
from app.core.ids import uuid7
from app.db.models import (
    Channel,
    Comment,
    ModerationStatus,
    ReactionValue,
    Subscription,
    User,
    Video,
    VideoReaction,
)
from app.identity.service import channel_for_user, public_channel
from app.social.rules import reaction_deltas

_COMMENT_PAGE = 20
_REPLY_EMBED = 20
SUBSCRIPTION_CURSOR = "subs"


def viewer_flags(
    session: Session, viewer: User, video: Video, channel: Channel
) -> tuple[str | None, bool]:
    reaction = session.get(VideoReaction, (viewer.id, video.id))
    subscribed = session.get(Subscription, (viewer.id, channel.id)) is not None
    value = None if reaction is None else reaction.value.value
    return value, subscribed


def set_reaction(
    session: Session, viewer: User, video_id: uuid.UUID, value: str
) -> dict[str, object]:
    video, channel = require_visible_video(session, video_id, viewer)
    locked = session.scalar(select(Video).where(Video.id == video.id).with_for_update())
    if locked is None:
        raise video_not_found()
    current = session.get(VideoReaction, (viewer.id, locked.id))
    previous = None if current is None else current.value.value
    new = None if value == "none" else value
    likes, dislikes = reaction_deltas(previous, new)
    locked.like_count += likes
    locked.dislike_count += dislikes
    now = datetime.now(UTC)
    if new is None:
        if current is not None:
            session.delete(current)
    elif current is None:
        session.add(
            VideoReaction(
                user_id=viewer.id,
                video_id=locked.id,
                value=ReactionValue(new),
                created_at=now,
                updated_at=now,
            )
        )
    else:
        current.value = ReactionValue(new)
        current.updated_at = now
    session.commit()
    return {
        "like_count": locked.like_count,
        "dislike_count": locked.dislike_count,
        "viewer_reaction": new,
        "channel_handle": channel.handle,
    }


def list_comments(
    session: Session, video_id: uuid.UUID, viewer: User | None, cursor: str | None, limit: int
) -> dict[str, object]:
    video, _channel = require_visible_video(session, video_id, viewer)
    filters = [
        Comment.video_id == video.id,
        Comment.parent_id.is_(None),
        Comment.moderation_status == ModerationStatus.visible,
    ]
    if cursor is not None:
        moment, comment_id = decode_time_id(cursor, "comments")
        filters.append(tuple_(Comment.created_at, Comment.id) < tuple_(moment, comment_id))
    rows = list(
        session.scalars(
            select(Comment)
            .where(*filters)
            .order_by(Comment.created_at.desc(), Comment.id.desc())
            .limit(limit + 1)
        ).all()
    )
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = encode_time_id("comments", last.created_at, last.id)
    visible = [row for row in page if row.deleted_at is None or _reply_count(session, row.id) > 0]
    return {"items": [_comment_item(session, row) for row in visible], "next_cursor": next_cursor}


def list_replies(
    session: Session, comment_id: uuid.UUID, viewer: User | None, cursor: str | None, limit: int
) -> dict[str, object]:
    parent = session.get(Comment, comment_id)
    missing = parent is None or parent.parent_id is not None
    if missing or parent.moderation_status is not ModerationStatus.visible:
        raise _comment_missing()
    require_visible_video(session, parent.video_id, viewer)
    if parent.deleted_at is not None and _reply_count(session, parent.id) == 0:
        raise _comment_missing()
    filters = [
        Comment.parent_id == parent.id,
        Comment.deleted_at.is_(None),
        Comment.moderation_status == ModerationStatus.visible,
    ]
    if cursor is not None:
        moment, reply_id = decode_time_id(cursor, "replies")
        filters.append(tuple_(Comment.created_at, Comment.id) > tuple_(moment, reply_id))
    rows = list(
        session.scalars(
            select(Comment)
            .where(*filters)
            .order_by(Comment.created_at.asc(), Comment.id.asc())
            .limit(limit + 1)
        ).all()
    )
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = encode_time_id("replies", last.created_at, last.id)
    items = [_comment_item(session, row, include_replies=False) for row in page]
    return {"items": items, "next_cursor": next_cursor}


def add_comment(
    session: Session, viewer: User, video_id: uuid.UUID, body: str, parent_id: uuid.UUID | None
) -> dict[str, object]:
    video, _channel = require_visible_video(session, video_id, viewer)
    locked = session.scalar(select(Video).where(Video.id == video.id).with_for_update())
    if locked is None:
        raise video_not_found()
    parent = None
    if parent_id is not None:
        parent = session.get(Comment, parent_id)
        if (
            parent is None
            or parent.video_id != locked.id
            or parent.deleted_at is not None
            or parent.moderation_status is not ModerationStatus.visible
        ):
            raise _comment_missing()
        if parent.parent_id is not None:
            raise AppError("INVALID_REQUEST", "Replies can only target a top-level comment.", 400)
    text = body.strip()
    if not text:
        raise AppError("INVALID_REQUEST", "Comment text is required.", 400)
    now = datetime.now(UTC)
    comment = Comment(
        id=uuid7(),
        video_id=locked.id,
        user_id=viewer.id,
        parent_id=None if parent is None else parent.id,
        body=text,
        moderation_status=ModerationStatus.visible,
        created_at=now,
        updated_at=now,
    )
    session.add(comment)
    locked.comment_count += 1
    session.commit()
    return _comment_item(session, comment, include_replies=False)


def edit_comment(
    session: Session, viewer: User, comment_id: uuid.UUID, body: str
) -> dict[str, object]:
    comment = _owned_comment(session, viewer, comment_id)
    text = body.strip()
    if not text:
        raise AppError("INVALID_REQUEST", "Comment text is required.", 400)
    now = datetime.now(UTC)
    if now < comment.created_at + timedelta(seconds=1):
        now = comment.created_at + timedelta(seconds=1)
    comment.body = text
    comment.updated_at = now
    session.commit()
    return _comment_item(session, comment, include_replies=False)


def delete_comment(session: Session, viewer: User, comment_id: uuid.UUID) -> None:
    comment = _owned_comment(session, viewer, comment_id)
    video = session.scalar(select(Video).where(Video.id == comment.video_id).with_for_update())
    if video is None:
        raise video_not_found()
    if comment.moderation_status is ModerationStatus.visible:
        video.comment_count = max(0, video.comment_count - 1)
    comment.deleted_at = datetime.now(UTC)
    session.commit()


def subscribe(session: Session, viewer: User, handle: str) -> None:
    found = public_channel(session, handle)
    channel = session.scalar(select(Channel).where(Channel.id == found.id).with_for_update())
    if channel is None:
        raise AppError("CHANNEL_NOT_FOUND", "Channel not found.", 404)
    if channel.user_id == viewer.id:
        raise AppError("INVALID_STATE", "You cannot subscribe to your own channel.", 409)
    existing = session.get(Subscription, (viewer.id, channel.id))
    if existing is not None:
        session.commit()
        return
    session.add(
        Subscription(
            subscriber_user_id=viewer.id, channel_id=channel.id, created_at=datetime.now(UTC)
        )
    )
    channel.subscriber_count += 1
    session.commit()


def unsubscribe(session: Session, viewer: User, handle: str) -> None:
    channel = public_channel(session, handle)
    locked = session.scalar(select(Channel).where(Channel.id == channel.id).with_for_update())
    if locked is None:
        return
    existing = session.get(Subscription, (viewer.id, locked.id))
    if existing is None:
        session.commit()
        return
    session.delete(existing)
    if locked.subscriber_count > 0:
        locked.subscriber_count -= 1
    session.commit()


def is_subscribed(session: Session, user_id: uuid.UUID, channel_id: uuid.UUID) -> bool:
    return session.get(Subscription, (user_id, channel_id)) is not None


def list_subscriptions(session: Session, viewer: User) -> list[Channel]:
    return list(
        session.scalars(
            select(Channel)
            .join(Subscription, Subscription.channel_id == Channel.id)
            .where(Subscription.subscriber_user_id == viewer.id)
            .order_by(Channel.handle)
        ).all()
    )


def list_subscribers(session: Session, viewer: User, handle: str) -> list[Channel]:
    channel = public_channel(session, handle)
    if channel.user_id != viewer.id:
        raise AppError("CHANNEL_NOT_FOUND", "Channel not found.", 404)
    users = session.scalars(
        select(User)
        .join(Subscription, Subscription.subscriber_user_id == User.id)
        .where(Subscription.channel_id == channel.id)
        .order_by(User.created_at)
    ).all()
    channels: list[Channel] = []
    for user in users:
        owned = channel_for_user(session, user.id)
        channels.append(owned)
    return channels


def subscription_feed(session: Session, viewer: User, cursor: str | None, limit: int) -> VideoPage:
    channel_ids = select(Subscription.channel_id).where(
        Subscription.subscriber_user_id == viewer.id
    )
    filters = [Video.channel_id.in_(channel_ids), *public_discovery_filters()]
    if cursor is not None:
        moment, video_id = decode_time_id(cursor, SUBSCRIPTION_CURSOR)
        filters.append(tuple_(Video.published_at, Video.id) < tuple_(moment, video_id))
    rows = session.execute(
        select(Video, Channel)
        .join(Channel, Channel.id == Video.channel_id)
        .where(*filters)
        .order_by(Video.published_at.desc(), Video.id.desc())
        .limit(limit + 1)
    ).all()
    return _page_from_rows(list(rows), limit, SUBSCRIPTION_CURSOR, include_status=False)


def _owned_comment(session: Session, viewer: User, comment_id: uuid.UUID) -> Comment:
    comment = session.get(Comment, comment_id)
    if comment is None or comment.deleted_at is not None:
        raise _comment_missing()
    require_visible_video(session, comment.video_id, viewer)
    if comment.user_id != viewer.id:
        raise AppError("FORBIDDEN", "You can only change your own comment.", 403)
    if comment.moderation_status is not ModerationStatus.visible:
        raise _comment_missing()
    return comment


def _comment_item(
    session: Session, comment: Comment, *, include_replies: bool = True
) -> dict[str, object]:
    deleted = comment.deleted_at is not None
    author = _author(session, comment.user_id)
    replies: list[dict[str, object]] = []
    reply_count = 0
    if comment.parent_id is None:
        reply_count = _reply_count(session, comment.id)
        if include_replies and reply_count:
            rows = session.scalars(
                select(Comment)
                .where(
                    Comment.parent_id == comment.id,
                    Comment.deleted_at.is_(None),
                    Comment.moderation_status == ModerationStatus.visible,
                )
                .order_by(Comment.created_at.asc(), Comment.id.asc())
                .limit(_REPLY_EMBED)
            ).all()
            replies = [_comment_item(session, row, include_replies=False) for row in rows]
    edited = comment.updated_at >= comment.created_at + timedelta(seconds=1)
    return {
        "id": comment.id,
        "body": "Comment deleted" if deleted else comment.body,
        "created_at": comment.created_at,
        "edited": False if deleted else edited,
        "deleted": deleted,
        "author": None if deleted else author,
        "reply_count": reply_count,
        "replies": replies,
    }


def _author(session: Session, user_id: uuid.UUID) -> dict[str, str] | None:
    channel = session.scalar(select(Channel).where(Channel.user_id == user_id))
    if channel is None:
        return None
    return {"handle": channel.handle, "name": channel.name}


def _reply_count(session: Session, comment_id: uuid.UUID) -> int:
    count = session.scalar(
        select(func.count())
        .select_from(Comment)
        .where(
            Comment.parent_id == comment_id,
            Comment.deleted_at.is_(None),
            Comment.moderation_status == ModerationStatus.visible,
        )
    )
    return int(count or 0)


def _comment_missing() -> AppError:
    return AppError("COMMENT_NOT_FOUND", "Comment not found.", 404)
