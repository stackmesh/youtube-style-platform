"""Video visibility rules. Later stages call this helper instead of reimplementing it."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.db.models import (
    Channel,
    ModerationStatus,
    ProcessingStatus,
    User,
    Video,
    VideoAccessGrant,
    Visibility,
)


def viewer_can_see(
    video: Video,
    viewer: User | None,
    *,
    owner_user_id: uuid.UUID,
    has_grant: bool,
) -> bool:
    """Return whether this caller may know the video exists.

    The owner can see every non-deleted state, including drafts. Everyone else needs a
    ready, visible, non-deleted video. Public and unlisted ids are then readable.
    Private ids also need an active grant. Authentication alone is not enough.
    """
    if video.deleted_at is not None:
        return False
    if viewer is not None and viewer.id == owner_user_id:
        return True
    if video.moderation_status is not ModerationStatus.visible:
        return False
    if video.processing_status is not ProcessingStatus.ready:
        return False
    if video.visibility in (Visibility.public, Visibility.unlisted):
        return True
    return video.visibility is Visibility.private and viewer is not None and has_grant


def public_discovery_filters() -> tuple[object, ...]:
    """Rows that may appear in the home feed, search, related list, and public channel list."""
    return (
        Video.processing_status == ProcessingStatus.ready,
        Video.moderation_status == ModerationStatus.visible,
        Video.visibility == Visibility.public,
        Video.deleted_at.is_(None),
    )


def require_visible_video(
    session: Session, video_id: uuid.UUID, viewer: User | None
) -> tuple[Video, Channel]:
    """Load a video the caller may see, or raise 404 without confirming that the id exists."""
    video = session.get(Video, video_id)
    if video is None:
        raise video_not_found()
    channel = session.get(Channel, video.channel_id)
    if channel is None:
        raise video_not_found()
    has_grant = False
    if (
        viewer is not None
        and viewer.id != channel.user_id
        and video.visibility is Visibility.private
        and video.deleted_at is None
    ):
        has_grant = active_grant_exists(session, video.id, viewer.id)
    if not viewer_can_see(video, viewer, owner_user_id=channel.user_id, has_grant=has_grant):
        raise video_not_found()
    return video, channel


def active_grant_exists(session: Session, video_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    grant_id = session.scalar(
        select(VideoAccessGrant.id).where(
            VideoAccessGrant.video_id == video_id,
            VideoAccessGrant.grantee_user_id == user_id,
            VideoAccessGrant.revoked_at.is_(None),
        )
    )
    return grant_id is not None


def video_not_found() -> AppError:
    return AppError("VIDEO_NOT_FOUND", "Video not found.", 404)
