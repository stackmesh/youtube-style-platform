import uuid
from datetime import UTC, datetime

import pytest

from app.catalogue.acl import viewer_can_see
from app.catalogue.cursors import decode_time_id, encode_time_id
from app.catalogue.service import clean_tags
from app.core.exceptions import AppError
from app.core.ids import uuid7
from app.db.models import ModerationStatus, ProcessingStatus, User, UserRole, Video, Visibility


def _video(**overrides: object) -> Video:
    values: dict[str, object] = {
        "id": uuid7(),
        "channel_id": uuid7(),
        "title": "Title",
        "description": "",
        "visibility": Visibility.private,
        "processing_status": ProcessingStatus.draft,
        "moderation_status": ModerationStatus.visible,
        "acl_version": 1,
        "view_count": 0,
        "like_count": 0,
        "dislike_count": 0,
        "comment_count": 0,
    }
    values.update(overrides)
    return Video(**values)


def _user() -> User:
    return User(
        id=uuid7(),
        email="owner@example.com",
        password_hash="hash",
        role=UserRole.USER,
        is_active=True,
        session_epoch=1,
    )


def test_owner_can_see_a_draft_and_strangers_cannot() -> None:
    owner = _user()
    video = _video(visibility=Visibility.public)
    assert viewer_can_see(video, owner, owner_user_id=owner.id, has_grant=False) is True
    assert viewer_can_see(video, None, owner_user_id=owner.id, has_grant=False) is False
    assert viewer_can_see(video, _user(), owner_user_id=owner.id, has_grant=False) is False


def test_ready_unlisted_video_is_readable_by_id() -> None:
    video = _video(
        visibility=Visibility.unlisted,
        processing_status=ProcessingStatus.ready,
        published_at=datetime.now(UTC),
    )
    assert viewer_can_see(video, None, owner_user_id=uuid7(), has_grant=False) is True


def test_private_video_requires_a_grant() -> None:
    video = _video(
        visibility=Visibility.private,
        processing_status=ProcessingStatus.ready,
        published_at=datetime.now(UTC),
    )
    stranger = _user()
    assert viewer_can_see(video, stranger, owner_user_id=uuid7(), has_grant=False) is False
    assert viewer_can_see(video, stranger, owner_user_id=uuid7(), has_grant=True) is True
    assert viewer_can_see(video, None, owner_user_id=uuid7(), has_grant=True) is False


def test_hidden_and_deleted_videos_stay_hidden_from_strangers() -> None:
    owner = _user()
    hidden = _video(
        visibility=Visibility.public,
        processing_status=ProcessingStatus.ready,
        moderation_status=ModerationStatus.hidden,
        published_at=datetime.now(UTC),
    )
    deleted = _video(deleted_at=datetime.now(UTC))
    assert viewer_can_see(hidden, None, owner_user_id=owner.id, has_grant=False) is False
    assert viewer_can_see(hidden, owner, owner_user_id=owner.id, has_grant=False) is True
    assert viewer_can_see(deleted, owner, owner_user_id=owner.id, has_grant=False) is False


def test_tags_are_normalized_and_limited() -> None:
    assert clean_tags([" Travel ", "travel", "night-drive"]) == ["travel", "night-drive"]
    with pytest.raises(AppError) as caught:
        clean_tags(["Bad Tag"])
    assert caught.value.status_code == 422
    with pytest.raises(AppError):
        clean_tags([f"tag{index}" for index in range(16)])


def test_feed_cursor_round_trip_and_rejection() -> None:
    moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    video_id = uuid.UUID("0193b5a0-7c2a-7d2e-8c11-0a0b0c0d0e0f")
    token = encode_time_id("feed", moment, video_id)
    assert decode_time_id(token, "feed") == (moment, video_id)
    with pytest.raises(AppError) as caught:
        decode_time_id(token, "owned")
    assert caught.value.code == "INVALID_CURSOR"
    with pytest.raises(AppError):
        decode_time_id("not-a-cursor", "feed")
