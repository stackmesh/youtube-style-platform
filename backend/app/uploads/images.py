"""Re-encode owner images so stored thumbnails and avatars contain no source metadata."""

import uuid
from io import BytesIO

from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.db.models import Channel, User
from app.identity.service import channel_for_user
from app.uploads.objects import (
    StorageError,
    avatar_key,
    custom_thumbnail_key,
    delete_object,
    put_bytes,
)
from app.uploads.service import _owned_video

_MAX_BYTES = 2_000_000
_MAX_WIDTH = 1920
_MAX_HEIGHT = 1080


def store_custom_thumbnail(session: Session, owner: User, video_id: uuid.UUID, body: bytes) -> None:
    video, _channel = _owned_video(session, owner, video_id)
    encoded = jpeg_bytes(body)
    key = custom_thumbnail_key(str(video.id))
    try:
        put_bytes(key, encoded, "image/jpeg")
    except StorageError:
        raise AppError(
            "STORAGE_UNAVAILABLE",
            "Object storage could not store the image.",
            503,
        ) from None
    video.custom_thumbnail_key = key
    session.commit()


def delete_custom_thumbnail(session: Session, owner: User, video_id: uuid.UUID) -> None:
    video, _channel = _owned_video(session, owner, video_id)
    key = video.custom_thumbnail_key
    if key:
        try:
            delete_object(key)
        except StorageError:
            raise AppError(
                "STORAGE_UNAVAILABLE",
                "Object storage could not remove the image.",
                503,
            ) from None
    video.custom_thumbnail_key = None
    session.commit()


def store_avatar(session: Session, owner: User, body: bytes) -> Channel:
    channel = channel_for_user(session, owner.id)
    encoded = jpeg_bytes(body)
    key = avatar_key(str(channel.id))
    try:
        put_bytes(key, encoded, "image/jpeg")
    except StorageError:
        raise AppError(
            "STORAGE_UNAVAILABLE",
            "Object storage could not store the image.",
            503,
        ) from None
    channel.avatar_object_key = key
    session.commit()
    return channel


def delete_avatar(session: Session, owner: User) -> Channel:
    channel = channel_for_user(session, owner.id)
    if channel.avatar_object_key:
        try:
            delete_object(channel.avatar_object_key)
        except StorageError:
            raise AppError(
                "STORAGE_UNAVAILABLE",
                "Object storage could not remove the image.",
                503,
            ) from None
    channel.avatar_object_key = None
    session.commit()
    return channel


def jpeg_bytes(body: bytes) -> bytes:
    if len(body) < 1 or len(body) > _MAX_BYTES:
        raise AppError("VALIDATION_ERROR", "The image must be between 1 byte and 2 MB.", 422)
    try:
        from PIL import Image
    except ImportError as exc:
        raise AppError("STORAGE_UNAVAILABLE", "Image processing is unavailable.", 503) from exc
    try:
        with Image.open(BytesIO(body)) as image:
            image.load()
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise AppError("VALIDATION_ERROR", "Use a JPEG, PNG, or WebP image.", 422)
            if image.width > _MAX_WIDTH or image.height > _MAX_HEIGHT:
                raise AppError("VALIDATION_ERROR", "The image is larger than 1920 by 1080.", 422)
            frame = image.convert("RGB")
            output = BytesIO()
            frame.save(output, format="JPEG", quality=85, optimize=True)
    except AppError:
        raise
    except (OSError, ValueError) as exc:
        raise AppError("VALIDATION_ERROR", "The image could not be read.", 422) from exc
    return output.getvalue()
