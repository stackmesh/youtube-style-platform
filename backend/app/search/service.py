"""Catalogue search. Channel prefix matches sit beside the simple text search."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Numeric, and_, func, literal, or_, select, tuple_
from sqlalchemy.orm import Session

from app.catalogue.acl import public_discovery_filters
from app.catalogue.cursors import decode_cursor, encode_cursor, invalid_cursor
from app.db.models import Channel, Video

_VIDEO_KINDS = {"relevance": "video-relevance", "newest": "video-newest", "views": "video-views"}
_CHANNEL_KIND = "channel-relevance"


@dataclass(frozen=True)
class VideoHit:
    video: Video
    channel: Channel


@dataclass(frozen=True)
class VideoHits:
    items: tuple[VideoHit, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class ChannelHit:
    handle: str
    name: str


@dataclass(frozen=True)
class ChannelHits:
    items: tuple[ChannelHit, ...]
    next_cursor: str | None


def search_videos(
    session: Session, query: str, sort: str, cursor: str | None, limit: int
) -> VideoHits:
    rows = session.execute(video_search_select(query, sort, cursor).limit(limit + 1)).all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        next_cursor = _video_cursor(sort, page[-1])
    return VideoHits(
        tuple(VideoHit(video, channel) for video, channel, *_rest in page),
        next_cursor,
    )


def search_channels(session: Session, query: str, cursor: str | None, limit: int) -> ChannelHits:
    rows = session.execute(channel_search_select(query, cursor).limit(limit + 1)).all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        channel, rank = page[-1]
        next_cursor = encode_cursor(_CHANNEL_KIND, _rank_text(rank), str(channel.id))
    return ChannelHits(
        tuple(ChannelHit(handle=channel.handle, name=channel.name) for channel, _rank in page),
        next_cursor,
    )


def video_search_select(query: str, sort: str, cursor: str | None):
    tsquery = func.plainto_tsquery("simple", query)
    rank = func.ts_rank(Video.search_vector, tsquery)
    filters = [
        *public_discovery_filters(),
        Video.search_vector.op("@@")(tsquery),
    ]
    if sort == "newest":
        order = (Video.published_at.desc(), Video.id.desc())
        if cursor is not None:
            published_at, video_id = _decode_time(cursor, _VIDEO_KINDS["newest"])
            filters.append(tuple_(Video.published_at, Video.id) < tuple_(published_at, video_id))
    elif sort == "views":
        order = (Video.view_count.desc(), Video.id.desc())
        if cursor is not None:
            views, video_id = _decode_int(cursor, _VIDEO_KINDS["views"])
            filters.append(tuple_(Video.view_count, Video.id) < tuple_(views, video_id))
    else:
        order = (rank.desc(), Video.id.desc())
        if cursor is not None:
            cursor_rank, video_id = _decode_rank(cursor, _VIDEO_KINDS["relevance"])
            filters.append(
                or_(
                    rank < cursor_rank,
                    and_(rank == cursor_rank, Video.id < video_id),
                )
            )
    return (
        select(Video, Channel, rank)
        .join(Channel, Channel.id == Video.channel_id)
        .where(*filters)
        .order_by(*order)
    )


def channel_search_select(query: str, cursor: str | None):
    tsquery = func.plainto_tsquery("simple", query)
    rank = func.ts_rank(Channel.search_vector, tsquery)
    filters = [
        or_(
            Channel.search_vector.op("@@")(tsquery),
            Channel.handle.ilike(_like_prefix(query), escape="\\"),
        )
    ]
    if cursor is not None:
        cursor_rank, channel_id = _decode_rank(cursor, _CHANNEL_KIND)
        filters.append(or_(rank < cursor_rank, and_(rank == cursor_rank, Channel.id < channel_id)))
    return select(Channel, rank).where(*filters).order_by(rank.desc(), Channel.id.desc())


def _video_cursor(sort: str, row: tuple[object, ...]) -> str:
    video = row[0]
    if not isinstance(video, Video):
        raise invalid_cursor()
    if sort == "newest":
        if video.published_at is None:
            raise invalid_cursor()
        return encode_cursor(_VIDEO_KINDS["newest"], video.published_at.isoformat(), str(video.id))
    if sort == "views":
        return encode_cursor(_VIDEO_KINDS["views"], str(video.view_count), str(video.id))
    return encode_cursor(_VIDEO_KINDS["relevance"], _rank_text(row[2]), str(video.id))


def _decode_time(token: str, kind: str) -> tuple[datetime, uuid.UUID]:
    left, right = decode_cursor(token, kind)
    try:
        moment = datetime.fromisoformat(left)
        if moment.tzinfo is None:
            raise ValueError
        return moment, uuid.UUID(right)
    except ValueError:
        raise invalid_cursor() from None


def _decode_int(token: str, kind: str) -> tuple[int, uuid.UUID]:
    left, right = decode_cursor(token, kind)
    try:
        return int(left), uuid.UUID(right)
    except ValueError:
        raise invalid_cursor() from None


def _decode_rank(token: str, kind: str) -> tuple[object, uuid.UUID]:
    left, right = decode_cursor(token, kind)
    try:
        float(left)
        return literal(left).cast(Numeric), uuid.UUID(right)
    except ValueError:
        raise invalid_cursor() from None


def _rank_text(rank: object) -> str:
    return format(float(rank), ".10f")


def _like_prefix(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"
