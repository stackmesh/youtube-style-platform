"""Opaque keyset cursors. They are not signed and they are not authorization tokens."""

import base64
import binascii
import uuid
from datetime import datetime

from app.core.exceptions import AppError


def encode_cursor(kind: str, left: str, right: str) -> str:
    raw = f"{kind}|{left}|{right}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(token: str, kind: str) -> tuple[str, str]:
    try:
        padded = token + ("=" * (-len(token) % 4))
        text = base64.urlsafe_b64decode(padded.encode()).decode()
        prefix, left, right = text.split("|", 2)
        if prefix != kind or not left or not right:
            raise ValueError
        return left, right
    except (ValueError, UnicodeError, binascii.Error):
        raise invalid_cursor() from None


def encode_time_id(kind: str, moment: datetime, video_id: uuid.UUID) -> str:
    return encode_cursor(kind, moment.isoformat(), str(video_id))


def decode_time_id(token: str, kind: str) -> tuple[datetime, uuid.UUID]:
    left, right = decode_cursor(token, kind)
    try:
        moment = datetime.fromisoformat(left)
        if moment.tzinfo is None:
            raise ValueError
        return moment, uuid.UUID(right)
    except ValueError:
        raise invalid_cursor() from None


def invalid_cursor() -> AppError:
    return AppError("INVALID_CURSOR", "The cursor is not valid.", 400)
