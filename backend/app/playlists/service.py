"""Playlists. Reading a playlist never grants access to a video the caller cannot watch."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.catalogue.acl import require_visible_video, video_not_found, viewer_can_see
from app.core.exceptions import AppError
from app.core.ids import uuid7
from app.db.models import Channel, Playlist, PlaylistItem, PlaylistVisibility, User, Video
from app.identity.service import channel_for_user, public_channel
from app.social.rules import end_position


def create_playlist(
    session: Session, owner: User, title: str, description: str, visibility: PlaylistVisibility
) -> Playlist:
    channel = channel_for_user(session, owner.id)
    now = datetime.now(UTC)
    playlist = Playlist(
        id=uuid7(),
        channel_id=channel.id,
        title=title.strip(),
        description=description.strip(),
        visibility=visibility,
        created_at=now,
        updated_at=now,
    )
    if not playlist.title:
        raise AppError("INVALID_REQUEST", "A playlist title is required.", 400)
    session.add(playlist)
    session.commit()
    return playlist


def read_playlist(
    session: Session, playlist_id: uuid.UUID, viewer: User | None
) -> dict[str, object]:
    playlist, channel = _visible_playlist(session, playlist_id, viewer)
    items = list(
        session.scalars(
            select(PlaylistItem)
            .where(PlaylistItem.playlist_id == playlist.id)
            .order_by(PlaylistItem.position, PlaylistItem.video_id)
        ).all()
    )
    owner = viewer is not None and viewer.id == channel.user_id
    rendered: list[dict[str, object]] = []
    for item in items:
        card = _item_for_viewer(session, item, viewer, owner_view=owner)
        if card is not None:
            rendered.append(card)
    return {
        "id": playlist.id,
        "title": playlist.title,
        "description": playlist.description,
        "visibility": playlist.visibility,
        "channel": {"handle": channel.handle, "name": channel.name},
        "items": rendered,
    }


def update_playlist(
    session: Session,
    owner: User,
    playlist_id: uuid.UUID,
    title: str | None,
    description: str | None,
    visibility: PlaylistVisibility | None,
) -> Playlist:
    playlist = _owned(session, owner, playlist_id)
    if title is not None:
        cleaned = title.strip()
        if not cleaned:
            raise AppError("INVALID_REQUEST", "A playlist title is required.", 400)
        playlist.title = cleaned
    if description is not None:
        playlist.description = description.strip()
    if visibility is not None:
        playlist.visibility = visibility
    playlist.updated_at = datetime.now(UTC)
    session.commit()
    return playlist


def delete_playlist(session: Session, owner: User, playlist_id: uuid.UUID) -> None:
    playlist = _owned(session, owner, playlist_id)
    session.execute(delete(PlaylistItem).where(PlaylistItem.playlist_id == playlist.id))
    session.delete(playlist)
    session.commit()


def add_item(session: Session, owner: User, playlist_id: uuid.UUID, video_id: uuid.UUID) -> None:
    playlist = _owned(session, owner, playlist_id)
    session.scalar(select(Playlist).where(Playlist.id == playlist.id).with_for_update())
    try:
        require_visible_video(session, video_id, owner)
    except AppError:
        raise video_not_found() from None
    existing = session.get(PlaylistItem, (playlist.id, video_id))
    if existing is not None:
        session.commit()
        return
    positions = list(
        session.scalars(
            select(PlaylistItem.position).where(PlaylistItem.playlist_id == playlist.id)
        ).all()
    )
    session.add(
        PlaylistItem(
            playlist_id=playlist.id,
            video_id=video_id,
            position=end_position(positions),
            created_at=datetime.now(UTC),
        )
    )
    playlist.updated_at = datetime.now(UTC)
    session.commit()


def remove_item(session: Session, owner: User, playlist_id: uuid.UUID, video_id: uuid.UUID) -> None:
    playlist = _owned(session, owner, playlist_id)
    item = session.get(PlaylistItem, (playlist.id, video_id))
    if item is not None:
        session.delete(item)
        playlist.updated_at = datetime.now(UTC)
    session.commit()


def reorder_items(
    session: Session, owner: User, playlist_id: uuid.UUID, video_ids: list[uuid.UUID]
) -> None:
    playlist = _owned(session, owner, playlist_id)
    session.scalar(select(Playlist).where(Playlist.id == playlist.id).with_for_update())
    current = list(
        session.scalars(select(PlaylistItem).where(PlaylistItem.playlist_id == playlist.id)).all()
    )
    current_ids = {item.video_id for item in current}
    if len(video_ids) != len(current) or set(video_ids) != current_ids:
        raise AppError(
            "INVALID_REQUEST", "The playlist order must include each current video once.", 422
        )
    by_video = {item.video_id: item for item in current}
    for item in current:
        item.position = item.position + 1_000_000
    session.flush()
    for index, video_id in enumerate(video_ids, start=1):
        by_video[video_id].position = index * 1000
    playlist.updated_at = datetime.now(UTC)
    session.commit()


def list_channel_playlists(session: Session, handle: str, viewer: User | None) -> list[Playlist]:
    channel = public_channel(session, handle)
    owner = viewer is not None and viewer.id == channel.user_id
    filters = [Playlist.channel_id == channel.id]
    if not owner:
        filters.append(Playlist.visibility == PlaylistVisibility.public)
    return list(
        session.scalars(
            select(Playlist)
            .where(*filters)
            .order_by(Playlist.created_at.desc(), Playlist.id.desc())
        ).all()
    )


def _visible_playlist(
    session: Session, playlist_id: uuid.UUID, viewer: User | None
) -> tuple[Playlist, Channel]:
    playlist = session.get(Playlist, playlist_id)
    if playlist is None:
        raise _missing()
    channel = session.get(Channel, playlist.channel_id)
    if channel is None:
        raise _missing()
    owner = viewer is not None and viewer.id == channel.user_id
    if playlist.visibility is PlaylistVisibility.private and not owner:
        raise _missing()
    visible = playlist.visibility in (PlaylistVisibility.public, PlaylistVisibility.unlisted)
    if visible or owner:
        return playlist, channel
    raise _missing()


def _item_for_viewer(
    session: Session, item: PlaylistItem, viewer: User | None, *, owner_view: bool
) -> dict[str, object] | None:
    video = session.get(Video, item.video_id)
    if video is None:
        return {"video_id": item.video_id, "unavailable": True} if owner_view else None
    channel = session.get(Channel, video.channel_id)
    if channel is None:
        return {"video_id": item.video_id, "unavailable": True} if owner_view else None
    has_grant = False
    from app.catalogue.acl import active_grant_exists

    if viewer is not None and viewer.id != channel.user_id and video.visibility.value == "private":
        has_grant = active_grant_exists(session, video.id, viewer.id)
    allowed = viewer_can_see(video, viewer, owner_user_id=channel.user_id, has_grant=has_grant)
    if allowed:
        return {
            "video_id": video.id,
            "title": video.title,
            "channel": {"handle": channel.handle, "name": channel.name},
            "unavailable": False,
        }
    if owner_view and (video.deleted_at is not None or video.moderation_status.value == "hidden"):
        return {"video_id": video.id, "unavailable": True}
    return None


def _owned(session: Session, owner: User, playlist_id: uuid.UUID) -> Playlist:
    playlist = session.get(Playlist, playlist_id)
    if playlist is None:
        raise _missing()
    channel = session.get(Channel, playlist.channel_id)
    if channel is None or channel.user_id != owner.id:
        raise _missing()
    return playlist


def _missing() -> AppError:
    return AppError("PLAYLIST_NOT_FOUND", "Playlist not found.", 404)
