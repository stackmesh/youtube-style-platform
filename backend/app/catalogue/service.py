"""Catalogue writes and reads. Upload and processing transitions stay in later stages."""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.catalogue.acl import public_discovery_filters, require_visible_video, video_not_found
from app.catalogue.cursors import decode_time_id, encode_time_id
from app.core.exceptions import AppError
from app.core.ids import uuid7
from app.db.models import (
    Channel,
    ProcessingStatus,
    User,
    Video,
    VideoAccessGrant,
    VideoTag,
    Visibility,
)
from app.identity.passwords import normalize_handle
from app.identity.service import channel_for_user, public_channel

_TAG = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}$")
_MAX_TAGS = 15
_RELATED_LIMIT = 20
FEED_CURSOR = "feed"
OWNED_CURSOR = "owned"
CHANNEL_CURSOR = "channel"


@dataclass(frozen=True)
class VideoCard:
    video: Video
    channel: Channel
    tags: tuple[str, ...] = ()
    include_status: bool = False


@dataclass(frozen=True)
class VideoPage:
    items: tuple[VideoCard, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class GrantView:
    handle: str
    created_at: datetime
    revoked_at: datetime | None


def clean_tags(tags: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        value = tag.strip().lower()
        if _TAG.fullmatch(value) is None:
            raise AppError(
                "VALIDATION_ERROR",
                "Tags use lowercase letters, digits, and hyphens, up to 30 characters.",
                422,
                [{"field": "tags", "issue": "invalid"}],
            )
        if value not in seen:
            seen.add(value)
            cleaned.append(value)
    if len(cleaned) > _MAX_TAGS:
        raise AppError(
            "VALIDATION_ERROR",
            "A video can have at most 15 tags.",
            422,
            [{"field": "tags", "issue": "too_many"}],
        )
    return cleaned


def clean_title(title: str) -> str:
    cleaned = title.strip()
    if not cleaned or len(cleaned) > 100:
        raise AppError(
            "VALIDATION_ERROR",
            "Title must be 1 to 100 characters.",
            422,
            [{"field": "title", "issue": "invalid_length"}],
        )
    return cleaned


def clean_description(description: str) -> str:
    if len(description) > 5000:
        raise AppError(
            "VALIDATION_ERROR",
            "Description must be at most 5000 characters.",
            422,
            [{"field": "description", "issue": "too_long"}],
        )
    return description


def create_video(
    session: Session,
    owner: User,
    title: str,
    description: str,
    tags: list[str],
    visibility: Visibility,
) -> VideoCard:
    channel = channel_for_user(session, owner.id)
    stored_tags = clean_tags(tags)
    video = Video(
        id=uuid7(),
        channel_id=channel.id,
        title=clean_title(title),
        description=clean_description(description),
        visibility=visibility,
        processing_status=ProcessingStatus.draft,
    )
    session.add(video)
    for tag in stored_tags:
        session.add(VideoTag(video_id=video.id, tag=tag))
    session.commit()
    return VideoCard(video, channel, tuple(stored_tags), include_status=True)


def update_video(
    session: Session,
    owner: User,
    video_id: uuid.UUID,
    *,
    title: str | None,
    description: str | None,
    tags: list[str] | None,
    visibility: Visibility | None,
) -> VideoCard:
    video, channel = _owned_video(session, owner, video_id)
    if title is not None:
        video.title = clean_title(title)
    if description is not None:
        video.description = clean_description(description)
    if visibility is not None and visibility != video.visibility:
        video.visibility = visibility
        video.acl_version += 1
    stored_tags = _replace_tags(session, video.id, tags)
    video.updated_at = datetime.now(UTC)
    session.commit()
    return VideoCard(video, channel, tuple(stored_tags), include_status=True)


def delete_video(session: Session, owner: User, video_id: uuid.UUID) -> None:
    video, _channel = _owned_video(session, owner, video_id)
    now = datetime.now(UTC)
    video.deleted_at = now
    video.updated_at = now
    session.commit()


def read_video(session: Session, video_id: uuid.UUID, viewer: User | None) -> VideoCard:
    video, channel = require_visible_video(session, video_id, viewer)
    tags = _tags_for(session, video.id)
    include_status = viewer is not None and viewer.id == channel.user_id
    return VideoCard(video, channel, tuple(tags), include_status=include_status)


def list_feed(session: Session, cursor: str | None, limit: int) -> VideoPage:
    statement = feed_select(cursor).limit(limit + 1)
    rows = session.execute(statement).all()
    return _page_from_rows(rows, limit, FEED_CURSOR, include_status=False)


def feed_select(cursor: str | None):
    filters = list(public_discovery_filters())
    if cursor is not None:
        published_at, video_id = decode_time_id(cursor, FEED_CURSOR)
        filters.append(tuple_(Video.published_at, Video.id) < tuple_(published_at, video_id))
    return (
        select(Video, Channel)
        .join(Channel, Channel.id == Video.channel_id)
        .where(*filters)
        .order_by(Video.published_at.desc(), Video.id.desc())
    )


def list_owned(session: Session, owner: User, cursor: str | None, limit: int) -> VideoPage:
    channel = channel_for_user(session, owner.id)
    return _list_channel_owner(session, channel, cursor, limit)


def list_channel_videos(
    session: Session, handle: str, viewer: User | None, cursor: str | None, limit: int
) -> VideoPage:
    channel = public_channel(session, handle)
    if viewer is not None and viewer.id == channel.user_id:
        return _list_channel_owner(session, channel, cursor, limit)
    filters = [*public_discovery_filters(), Video.channel_id == channel.id]
    if cursor is not None:
        published_at, video_id = decode_time_id(cursor, CHANNEL_CURSOR)
        filters.append(tuple_(Video.published_at, Video.id) < tuple_(published_at, video_id))
    rows = session.execute(
        select(Video, Channel)
        .join(Channel, Channel.id == Video.channel_id)
        .where(*filters)
        .order_by(Video.published_at.desc(), Video.id.desc())
        .limit(limit + 1)
    ).all()
    return _page_from_rows(rows, limit, CHANNEL_CURSOR, include_status=False)


def list_related(
    session: Session, video_id: uuid.UUID, viewer: User | None
) -> tuple[VideoCard, ...]:
    video, channel = require_visible_video(session, video_id, viewer)
    tags = _tags_for(session, video.id)
    chosen: list[VideoCard] = []
    seen: set[uuid.UUID] = {video.id}
    if tags:
        tag_ids = select(VideoTag.video_id).where(
            VideoTag.tag.in_(tags), VideoTag.video_id != video.id
        )
        rows = session.execute(
            select(Video, Channel)
            .join(Channel, Channel.id == Video.channel_id)
            .where(*public_discovery_filters(), Video.id.in_(tag_ids))
            .order_by(Video.published_at.desc(), Video.id.desc())
            .limit(_RELATED_LIMIT)
        ).all()
        for row_video, row_channel in rows:
            if row_video.id in seen:
                continue
            seen.add(row_video.id)
            chosen.append(VideoCard(row_video, row_channel))
    remaining = _RELATED_LIMIT - len(chosen)
    if remaining > 0:
        rows = session.execute(
            select(Video, Channel)
            .join(Channel, Channel.id == Video.channel_id)
            .where(
                *public_discovery_filters(),
                Video.channel_id == channel.id,
                Video.id.notin_(seen),
            )
            .order_by(Video.published_at.desc(), Video.id.desc())
            .limit(remaining)
        ).all()
        for row_video, row_channel in rows:
            chosen.append(VideoCard(row_video, row_channel))
    return tuple(chosen)


def list_grants(session: Session, owner: User, video_id: uuid.UUID) -> tuple[GrantView, ...]:
    video, _channel = _owned_video(session, owner, video_id)
    rows = session.execute(
        select(VideoAccessGrant, Channel.handle)
        .join(Channel, Channel.user_id == VideoAccessGrant.grantee_user_id)
        .where(VideoAccessGrant.video_id == video.id)
        .order_by(VideoAccessGrant.created_at.desc(), VideoAccessGrant.id.desc())
    ).all()
    return tuple(
        GrantView(handle=handle, created_at=grant.created_at, revoked_at=grant.revoked_at)
        for grant, handle in rows
    )


def grant_access(session: Session, owner: User, video_id: uuid.UUID, handle: str) -> GrantView:
    video, _channel = _owned_video(session, owner, video_id)
    grantee = _channel_for_handle(session, handle)
    if grantee.user_id == owner.id:
        raise AppError("GRANT_CONFLICT", "You cannot grant access to yourself.", 409)
    existing = session.scalar(
        select(VideoAccessGrant.id).where(
            VideoAccessGrant.video_id == video.id,
            VideoAccessGrant.grantee_user_id == grantee.user_id,
            VideoAccessGrant.revoked_at.is_(None),
        )
    )
    if existing is not None:
        raise AppError("GRANT_CONFLICT", "That viewer already has access.", 409)
    grant = VideoAccessGrant(
        id=uuid7(),
        video_id=video.id,
        grantee_user_id=grantee.user_id,
        granted_by_user_id=owner.id,
    )
    session.add(grant)
    video.acl_version += 1
    video.updated_at = datetime.now(UTC)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise AppError("GRANT_CONFLICT", "That viewer already has access.", 409) from None
    session.refresh(grant)
    return GrantView(handle=grantee.handle, created_at=grant.created_at, revoked_at=None)


def revoke_grant(session: Session, owner: User, video_id: uuid.UUID, handle: str) -> None:
    video, _channel = _owned_video(session, owner, video_id)
    grantee = _channel_for_handle(session, handle)
    grant = session.scalar(
        select(VideoAccessGrant).where(
            VideoAccessGrant.video_id == video.id,
            VideoAccessGrant.grantee_user_id == grantee.user_id,
            VideoAccessGrant.revoked_at.is_(None),
        )
    )
    if grant is None:
        raise AppError("GRANT_NOT_FOUND", "Grant not found.", 404)
    now = datetime.now(UTC)
    grant.revoked_at = now
    video.acl_version += 1
    video.updated_at = now
    session.commit()


def _owned_video(session: Session, owner: User, video_id: uuid.UUID) -> tuple[Video, Channel]:
    video = session.get(Video, video_id)
    if video is None or video.deleted_at is not None:
        raise video_not_found()
    channel = session.get(Channel, video.channel_id)
    if channel is None or channel.user_id != owner.id:
        raise video_not_found()
    return video, channel


def _channel_for_handle(session: Session, handle: str) -> Channel:
    channel = session.scalar(select(Channel).where(Channel.handle == normalize_handle(handle)))
    if channel is None:
        raise AppError("CHANNEL_NOT_FOUND", "Channel not found.", 404)
    return channel


def _replace_tags(session: Session, video_id: uuid.UUID, tags: list[str] | None) -> list[str]:
    if tags is None:
        return _tags_for(session, video_id)
    cleaned = clean_tags(tags)
    session.execute(delete(VideoTag).where(VideoTag.video_id == video_id))
    for tag in cleaned:
        session.add(VideoTag(video_id=video_id, tag=tag))
    return cleaned


def _tags_for(session: Session, video_id: uuid.UUID) -> list[str]:
    return list(
        session.scalars(
            select(VideoTag.tag).where(VideoTag.video_id == video_id).order_by(VideoTag.tag)
        ).all()
    )


def _list_channel_owner(
    session: Session, channel: Channel, cursor: str | None, limit: int
) -> VideoPage:
    filters = [Video.channel_id == channel.id, Video.deleted_at.is_(None)]
    if cursor is not None:
        created_at, video_id = decode_time_id(cursor, OWNED_CURSOR)
        filters.append(tuple_(Video.created_at, Video.id) < tuple_(created_at, video_id))
    rows = session.execute(
        select(Video, Channel)
        .join(Channel, Channel.id == Video.channel_id)
        .where(*filters)
        .order_by(Video.created_at.desc(), Video.id.desc())
        .limit(limit + 1)
    ).all()
    return _page_from_rows(rows, limit, OWNED_CURSOR, include_status=True)


def _page_from_rows(
    rows: list[tuple[Video, Channel]], limit: int, kind: str, include_status: bool
) -> VideoPage:
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1][0]
        moment = last.created_at if kind == OWNED_CURSOR else last.published_at
        if moment is None:
            raise video_not_found()
        next_cursor = encode_time_id(kind, moment, last.id)
    items = tuple(
        VideoCard(video, channel, include_status=include_status) for video, channel in page
    )
    return VideoPage(items, next_cursor)
