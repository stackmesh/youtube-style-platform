"""Authorize playback, serve private HLS, and count a view after playback starts."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.catalogue.acl import active_grant_exists, video_not_found, viewer_can_see
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.db.models import (
    Channel,
    ModerationStatus,
    PlaybackSession,
    ProcessingStatus,
    User,
    UserRole,
    UserStorageQuota,
    Video,
    VideoReport,
    VideoViewDedupe,
    Visibility,
)
from app.infrastructure.redis import redis_client
from app.uploads.hls import sanitize_master, sanitize_variant
from app.uploads.objects import (
    StorageError,
    delete_prefix,
    get_bytes,
    head_size,
    prefix_size,
    presign_get,
)

SESSION_TTL = timedelta(hours=6)
_VIEW_WINDOW = timedelta(hours=6)
_VIEW_RETENTION = timedelta(hours=48)
_SALT_TTL = 48 * 60 * 60
_ALLOWED_RENDITIONS = {"360", "720", "1080"}


def open_playback(
    session: Session,
    video_id: uuid.UUID,
    viewer: User | None,
    cookie_value: str | None,
    *,
    moderation_access: bool = False,
    commit: bool = True,
) -> PlaybackSession:
    if moderation_access:
        video = _require_ready(session, video_id)
    else:
        video = _require_playable(session, video_id, viewer)
    now = datetime.now(UTC)
    current = _matching_session(session, video_id, cookie_value, viewer)
    if current is not None and current.expires_at > now and current.revoked_at is None:
        current.expires_at = now + SESSION_TTL
        current.moderation_access = current.moderation_access or moderation_access
        if commit:
            session.commit()
        return current
    if viewer is not None:
        session.execute(
            update(PlaybackSession)
            .where(
                PlaybackSession.user_id == viewer.id,
                PlaybackSession.video_id == video.id,
                PlaybackSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
    created = PlaybackSession(
        id=uuid.uuid4(),
        user_id=None if viewer is None else viewer.id,
        video_id=video.id,
        expires_at=now + SESSION_TTL,
        moderation_access=moderation_access,
        created_at=now,
    )
    session.add(created)
    if commit:
        session.commit()
    else:
        session.flush()
    return created


def end_playback(
    session: Session, video_id: uuid.UUID, viewer: User | None, cookie_value: str | None
) -> None:
    now = datetime.now(UTC)
    current = _matching_session(session, video_id, cookie_value, viewer)
    if current is not None and current.revoked_at is None:
        current.revoked_at = now
    if viewer is not None:
        session.execute(
            update(PlaybackSession)
            .where(
                PlaybackSession.user_id == viewer.id,
                PlaybackSession.video_id == video_id,
                PlaybackSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
    session.commit()


def revoke_user_playback(session: Session, user_id: uuid.UUID, now: datetime) -> None:
    session.execute(
        update(PlaybackSession)
        .where(PlaybackSession.user_id == user_id, PlaybackSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )


def revoke_video_playback(session: Session, video_id: uuid.UUID, now: datetime) -> None:
    session.execute(
        update(PlaybackSession)
        .where(PlaybackSession.video_id == video_id, PlaybackSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )


def master_playlist(
    session: Session, video_id: uuid.UUID, viewer: User | None, cookie_value: str | None
) -> str:
    _authorized(session, video_id, viewer, cookie_value)
    return _playlist_text(f"hls/{video_id}/master.m3u8", sanitize_master)


def variant_playlist(
    session: Session,
    video_id: uuid.UUID,
    rendition: str,
    viewer: User | None,
    cookie_value: str | None,
) -> str:
    video = _authorized(session, video_id, viewer, cookie_value)
    name = _rendition_name(rendition)
    if name not in _published_renditions(video):
        raise video_not_found()
    return _playlist_text(f"hls/{video_id}/{name}/index.m3u8", sanitize_variant)


def segment_redirect(
    session: Session,
    video_id: uuid.UUID,
    rendition: str,
    segment: str,
    viewer: User | None,
    cookie_value: str | None,
) -> str:
    video = _authorized(session, video_id, viewer, cookie_value)
    name = _rendition_name(rendition)
    segment_name = _segment_name(segment)
    if name not in _published_renditions(video):
        raise video_not_found()
    if segment_name not in _published_segments(video_id, name):
        raise video_not_found()
    key = f"hls/{video_id}/{name}/{segment_name}"
    try:
        url = presign_get(key)
    except StorageError as exc:
        raise _unavailable() from exc
    expected = urlparse(get_settings().s3_public_endpoint_url).hostname
    if not expected or urlparse(url).hostname != expected:
        raise _unavailable()
    return url


def public_thumbnail_key(session: Session, video_id: uuid.UUID) -> str:
    video = session.get(Video, video_id)
    if (
        video is None
        or video.deleted_at is not None
        or video.visibility is not Visibility.public
        or video.processing_status is not ProcessingStatus.ready
        or video.moderation_status is not ModerationStatus.visible
    ):
        raise video_not_found()
    allowed = {
        f"thumbnails/{video.id}/custom.jpg": video.custom_thumbnail_key,
        f"thumbnails/{video.id}/auto.jpg": video.auto_thumbnail_key,
    }
    key = allowed[f"thumbnails/{video.id}/custom.jpg"] or allowed[f"thumbnails/{video.id}/auto.jpg"]
    if key not in allowed or allowed[key] != key:
        raise video_not_found()
    try:
        if head_size(key) is None:
            raise video_not_found()
    except StorageError as exc:
        raise _unavailable() from exc
    return key


def record_view(
    session: Session,
    video_id: uuid.UUID,
    viewer: User | None,
    cookie_value: str | None,
    ip_address: str,
    user_agent: str,
) -> None:
    """Increment view_count once per viewer inside six hours, after playback has started."""
    video = _authorized(session, video_id, viewer, cookie_value)
    key = _viewer_key(viewer, ip_address, user_agent)
    now = datetime.now(UTC)
    session.scalar(select(Video).where(Video.id == video.id).with_for_update())
    row = session.get(VideoViewDedupe, (video.id, key))
    if row is not None and now - row.last_viewed_at < _VIEW_WINDOW:
        session.commit()
        return
    if row is None:
        session.add(VideoViewDedupe(video_id=video.id, viewer_key=key, last_viewed_at=now))
    else:
        row.last_viewed_at = now
    session.execute(
        update(Video).where(Video.id == video.id).values(view_count=Video.view_count + 1)
    )
    session.commit()


def release_deleted_quota(session: Session, owner: User, video: Video) -> None:
    """Return stored bytes to the owner. Storage failure leaves the quota unchanged."""
    try:
        amount = _stored_bytes(video)
    except StorageError:
        return
    if amount <= 0:
        return
    quota = session.scalar(
        select(UserStorageQuota).where(UserStorageQuota.user_id == owner.id).with_for_update()
    )
    if quota is None:
        return
    quota.bytes_committed = max(0, quota.bytes_committed - amount)


def maintain_playback(session: Session) -> None:
    now = datetime.now(UTC)
    session.execute(delete(PlaybackSession).where(PlaybackSession.expires_at < now))
    session.execute(
        delete(VideoViewDedupe).where(VideoViewDedupe.last_viewed_at < now - _VIEW_RETENTION)
    )
    session.commit()
    rows = session.scalars(
        select(Video)
        .where(Video.deleted_at.is_not(None), Video.storage_released_at.is_(None))
        .order_by(Video.deleted_at.desc())
        .limit(20)
    ).all()
    for video in rows:
        try:
            delete_prefix(f"originals/{video.id}/")
            delete_prefix(f"hls/{video.id}/")
            delete_prefix(f"thumbnails/{video.id}/")
        except StorageError:
            session.rollback()
            return
        current = session.get(Video, video.id)
        if current is None:
            continue
        current.storage_released_at = datetime.now(UTC)
        session.commit()


def _require_ready(session: Session, video_id: uuid.UUID) -> Video:
    video = session.get(Video, video_id)
    if (
        video is None
        or video.deleted_at is not None
        or video.processing_status is not ProcessingStatus.ready
    ):
        raise video_not_found()
    return video


def _moderation_session_allowed(session: Session, video: Video, viewer: User | None) -> bool:
    if viewer is None or viewer.role not in (UserRole.MODERATOR, UserRole.ADMIN):
        return False
    if video.deleted_at is not None or video.processing_status is not ProcessingStatus.ready:
        return False
    if video.moderation_status is ModerationStatus.hidden:
        return True
    if video.visibility in (Visibility.public, Visibility.unlisted):
        return True
    report_id = session.scalar(
        select(VideoReport.id).where(VideoReport.video_id == video.id).limit(1)
    )
    return report_id is not None


def _require_playable(session: Session, video_id: uuid.UUID, viewer: User | None) -> Video:
    decision, video = _access(session, video_id, viewer)
    if decision == "not_ready":
        raise AppError("VIDEO_NOT_READY", "Video is not ready.", 409)
    if decision != "ok" or video is None:
        raise video_not_found()
    return video


def _authorized(
    session: Session, video_id: uuid.UUID, viewer: User | None, cookie_value: str | None
) -> Video:
    current = _matching_session(session, video_id, cookie_value, viewer)
    now = datetime.now(UTC)
    if current is None or current.revoked_at is not None or current.expires_at <= now:
        raise video_not_found()
    if current.moderation_access:
        video = session.get(Video, video_id)
        if video is None or not _moderation_session_allowed(session, video, viewer):
            current.revoked_at = now
            session.commit()
            raise video_not_found()
        return video
    decision, video = _access(session, video_id, viewer)
    if decision != "ok" or video is None:
        current.revoked_at = now
        session.commit()
        raise video_not_found()
    return video


def _matching_session(
    session: Session, video_id: uuid.UUID, cookie_value: str | None, viewer: User | None
) -> PlaybackSession | None:
    if not cookie_value:
        return None
    try:
        identifier = uuid.UUID(cookie_value)
    except ValueError:
        return None
    if identifier.version != 4:
        return None
    row = session.get(PlaybackSession, identifier)
    if row is None or row.video_id != video_id:
        return None
    expected = None if viewer is None else viewer.id
    if row.user_id != expected:
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
            session.commit()
        return None
    return row


def _access(session: Session, video_id: uuid.UUID, viewer: User | None) -> tuple[str, Video | None]:
    video = session.get(Video, video_id)
    if video is None or video.deleted_at is not None:
        return "missing", None
    channel = session.get(Channel, video.channel_id)
    if channel is None:
        return "missing", None
    has_grant = False
    if (
        viewer is not None
        and viewer.id != channel.user_id
        and video.visibility is Visibility.private
    ):
        has_grant = active_grant_exists(session, video.id, viewer.id)
    if not viewer_can_see(video, viewer, owner_user_id=channel.user_id, has_grant=has_grant):
        return "missing", None
    if video.processing_status is not ProcessingStatus.ready:
        if viewer is not None and viewer.id == channel.user_id:
            return "not_ready", video
        return "missing", None
    return "ok", video


def _published_renditions(video: Video) -> set[str]:
    if video.hls_master_key != f"hls/{video.id}/master.m3u8":
        return set()
    body = _object_text(video.hls_master_key)
    names: set[str] = set()
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.endswith("/index.m3u8") and stripped.split("/", 1)[0] in _ALLOWED_RENDITIONS:
            names.add(stripped.split("/", 1)[0])
    return names


def _published_segments(video_id: uuid.UUID, rendition: str) -> set[str]:
    body = _object_text(f"hls/{video_id}/{rendition}/index.m3u8")
    return {
        line.strip()
        for line in body.splitlines()
        if line.strip().startswith("segment_") and line.strip().endswith(".ts")
    }


def _playlist_text(key: str, sanitizer) -> str:
    try:
        return sanitizer(_object_text(key))
    except StorageError as exc:
        raise _unavailable() from exc


def _object_text(key: str) -> str:
    try:
        payload = get_bytes(key)
    except StorageError as exc:
        raise _unavailable() from exc
    if payload is None:
        raise _unavailable()
    return payload.decode("utf-8", errors="replace")


def _rendition_name(value: str) -> str:
    if value not in _ALLOWED_RENDITIONS:
        raise video_not_found()
    return value


def _segment_name(value: str) -> str:
    if len(value) != 16 or not value.startswith("segment_") or not value.endswith(".ts"):
        raise video_not_found()
    digits = value[8:13]
    if not digits.isdigit():
        raise video_not_found()
    return value


def _viewer_key(viewer: User | None, ip_address: str, user_agent: str) -> bytes:
    if viewer is not None:
        material = f"user:{viewer.id}"
    else:
        material = f"anon:{_daily_salt()}:{ip_address}:{user_agent[:200]}"
    return hashlib.sha256(material.encode("utf-8")).digest()


def _daily_salt() -> str:
    from redis.exceptions import RedisError

    today = datetime.now(UTC).date().isoformat()
    key = f"view:salt:{today}"
    try:
        client = redis_client()
        current = client.get(key)
        if isinstance(current, str) and current:
            return current
        created = uuid.uuid4().hex
        if client.set(key, created, ex=_SALT_TTL, nx=True):
            return created
        current = client.get(key)
    except RedisError as exc:
        raise AppError("SERVICE_UNAVAILABLE", "Playback is unavailable.", 503) from exc
    if isinstance(current, str) and current:
        return current
    raise AppError("SERVICE_UNAVAILABLE", "Playback is unavailable.", 503)


def _stored_bytes(video: Video) -> int:
    total = 0
    for prefix in (
        f"originals/{video.id}/",
        f"hls/{video.id}/",
        f"thumbnails/{video.id}/",
    ):
        total += prefix_size(prefix)
    if total == 0 and video.source_byte_size:
        return int(video.source_byte_size)
    return total


def _unavailable() -> AppError:
    return AppError("STORAGE_UNAVAILABLE", "Playback is unavailable.", 503)
