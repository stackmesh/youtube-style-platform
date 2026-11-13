"""Engagement, playlists, and moderation against the shared Compose database."""

import uuid
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.db.migrate import upgrade_head
from app.db.models import (
    ModerationAction,
    PlaybackSession,
    ProcessingStatus,
    User,
    UserRole,
    Video,
    Visibility,
)
from app.db.session import SessionLocal
from app.infrastructure.redis import redis_client
from app.main import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clear_rate_limits() -> None:
    cache = redis_client()
    keys = list(cache.scan_iter("rl:*"))
    if keys:
        cache.delete(*keys)


def _register(client: TestClient, name: str) -> dict[str, str]:
    suffix = uuid.uuid4().hex[:8]
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"{name}-{suffix}@example.com",
            "password": "correct-password",
            "handle": f"{name[:1]}{suffix}",
            "name": name,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return {
        "token": body["access_token"],
        "id": body["user"]["id"],
        "handle": body["user"]["channel"]["handle"],
        "email": body["user"]["email"],
    }


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _video(client: TestClient, token: str, title: str, visibility: str = "public") -> str:
    response = client.post(
        "/api/v1/videos",
        headers=_auth(token),
        json={"title": title, "description": "", "tags": [], "visibility": visibility},
    )
    assert response.status_code == 200, response.text
    video_id = response.json()["id"]
    session = SessionLocal()
    try:
        video = session.get(Video, uuid.UUID(video_id))
        assert video is not None
        video.processing_status = ProcessingStatus.ready
        video.published_at = datetime(2000, 1, 1, tzinfo=UTC)
        video.visibility = Visibility(visibility)
        session.commit()
    finally:
        session.close()
    return video_id


def _role(user_id: str, role: UserRole) -> None:
    session = SessionLocal()
    try:
        user = session.get(User, uuid.UUID(user_id))
        assert user is not None
        user.role = role
        session.commit()
    finally:
        session.close()


def test_reactions_are_unique_and_switch_counters(client: TestClient) -> None:
    owner = _register(client, "react")
    video_id = _video(client, owner["token"], "reaction clip")
    first = client.put(
        f"/api/v1/videos/{video_id}/reaction",
        headers=_auth(owner["token"]),
        json={"value": "like"},
    )
    second = client.put(
        f"/api/v1/videos/{video_id}/reaction",
        headers=_auth(owner["token"]),
        json={"value": "like"},
    )
    switched = client.put(
        f"/api/v1/videos/{video_id}/reaction",
        headers=_auth(owner["token"]),
        json={"value": "dislike"},
    )
    assert first.status_code == 200, first.text
    assert second.json()["like_count"] == 1
    assert switched.json()["like_count"] == 0
    assert switched.json()["dislike_count"] == 1
    assert switched.json()["viewer_reaction"] == "dislike"
    session = SessionLocal()
    try:
        with pytest.raises(IntegrityError):
            session.execute(
                text("UPDATE videos SET like_count = like_count - 1 WHERE id = :id"),
                {"id": video_id},
            )
            session.commit()
    finally:
        session.rollback()
        session.close()


def test_comments_stay_on_accessible_videos(client: TestClient) -> None:
    owner = _register(client, "cowner")
    stranger = _register(client, "cstr")
    private_id = _video(client, owner["token"], "private comments", "private")
    public_id = _video(client, owner["token"], "public comments", "public")
    hidden = client.get(f"/api/v1/videos/{private_id}/comments")
    assert hidden.status_code == 404
    assert "private comments" not in hidden.text
    denied = client.post(
        f"/api/v1/videos/{private_id}/comments",
        headers=_auth(stranger["token"]),
        json={"body": "nope"},
    )
    assert denied.status_code == 404
    created = client.post(
        f"/api/v1/videos/{public_id}/comments",
        headers=_auth(owner["token"]),
        json={"body": "hello"},
    )
    assert created.status_code == 200, created.text
    comment_id = created.json()["id"]
    reply = client.post(
        f"/api/v1/videos/{public_id}/comments",
        headers=_auth(stranger["token"]),
        json={"body": "reply", "parent_id": comment_id},
    )
    assert reply.status_code == 200, reply.text
    nested = client.post(
        f"/api/v1/videos/{public_id}/comments",
        headers=_auth(owner["token"]),
        json={"body": "too deep", "parent_id": reply.json()["id"]},
    )
    assert nested.status_code == 400
    edited = client.patch(
        f"/api/v1/comments/{comment_id}",
        headers=_auth(stranger["token"]),
        json={"body": "stolen"},
    )
    assert edited.status_code == 403
    removed = client.delete(f"/api/v1/comments/{comment_id}", headers=_auth(stranger["token"]))
    assert removed.status_code == 403


def test_subscriptions_reject_self_and_duplicates(client: TestClient) -> None:
    owner = _register(client, "subown")
    viewer = _register(client, "subview")
    video_id = _video(client, owner["token"], "sub feed")
    own = client.put(
        f"/api/v1/channels/{owner['handle']}/subscription",
        headers=_auth(owner["token"]),
    )
    assert own.status_code == 409
    first = client.put(
        f"/api/v1/channels/{owner['handle']}/subscription",
        headers=_auth(viewer["token"]),
    )
    second = client.put(
        f"/api/v1/channels/{owner['handle']}/subscription",
        headers=_auth(viewer["token"]),
    )
    assert first.status_code == 204
    assert second.status_code == 204
    channel = client.get(f"/api/v1/channels/{owner['handle']}", headers=_auth(viewer["token"]))
    assert channel.json()["subscriber_count"] == 1
    assert channel.json()["viewer_subscribed"] is True
    strangers = client.get(
        f"/api/v1/channels/{owner['handle']}/subscribers",
        headers=_auth(viewer["token"]),
    )
    assert strangers.status_code == 404
    owned = client.get(
        f"/api/v1/channels/{owner['handle']}/subscribers",
        headers=_auth(owner["token"]),
    )
    assert owned.status_code == 200
    assert owned.json()[0]["handle"] == viewer["handle"]
    assert "email" not in owned.text
    feed = client.get("/api/v1/me/subscriptions/videos", headers=_auth(viewer["token"]))
    assert feed.status_code == 200
    assert video_id in [item["id"] for item in feed.json()["items"]]
    client.delete(
        f"/api/v1/channels/{owner['handle']}/subscription",
        headers=_auth(viewer["token"]),
    )
    after = client.get(f"/api/v1/channels/{owner['handle']}")
    assert after.json()["subscriber_count"] == 0


def test_playlists_hide_private_videos_and_keep_order(client: TestClient) -> None:
    owner = _register(client, "plist")
    stranger = _register(client, "plstr")
    public_id = _video(client, owner["token"], "shown in playlist", "public")
    private_id = _video(client, owner["token"], "secret in playlist", "private")
    created = client.post(
        "/api/v1/playlists",
        headers=_auth(owner["token"]),
        json={"title": "Mix", "description": "", "visibility": "public"},
    )
    assert created.status_code == 200, created.text
    playlist_id = created.json()["id"]
    assert (
        client.post(
            f"/api/v1/playlists/{playlist_id}/items",
            headers=_auth(owner["token"]),
            json={"video_id": public_id},
        ).status_code
        == 204
    )
    assert (
        client.post(
            f"/api/v1/playlists/{playlist_id}/items",
            headers=_auth(owner["token"]),
            json={"video_id": private_id},
        ).status_code
        == 204
    )
    other = _video(client, stranger["token"], "not watchable", "private")
    blocked = client.post(
        f"/api/v1/playlists/{playlist_id}/items",
        headers=_auth(owner["token"]),
        json={"video_id": other},
    )
    assert blocked.status_code == 404
    stranger_view = client.get(f"/api/v1/playlists/{playlist_id}")
    assert stranger_view.status_code == 200
    titles = [item.get("title") for item in stranger_view.json()["items"]]
    assert "shown in playlist" in titles
    assert "secret in playlist" not in stranger_view.text
    private_list = client.post(
        "/api/v1/playlists",
        headers=_auth(owner["token"]),
        json={"title": "Mine", "visibility": "private"},
    )
    assert client.get(f"/api/v1/playlists/{private_list.json()['id']}").status_code == 404
    stolen = client.patch(
        f"/api/v1/playlists/{playlist_id}",
        headers=_auth(stranger["token"]),
        json={"title": "Stolen"},
    )
    assert stolen.status_code == 404
    ordered = client.patch(
        f"/api/v1/playlists/{playlist_id}/items",
        headers=_auth(owner["token"]),
        json={"video_ids": [private_id, public_id]},
    )
    assert ordered.status_code == 204, ordered.text
    owner_view = client.get(f"/api/v1/playlists/{playlist_id}", headers=_auth(owner["token"]))
    assert [item["video_id"] for item in owner_view.json()["items"]] == [private_id, public_id]
    removed = client.delete(
        f"/api/v1/playlists/{private_list.json()['id']}", headers=_auth(owner["token"])
    )
    assert removed.status_code == 204
    assert client.get(f"/api/v1/videos/{public_id}").status_code == 200


def test_moderation_roles_audit_and_playback(client: TestClient) -> None:
    owner = _register(client, "modown")
    viewer = _register(client, "modview")
    moderator = _register(client, "modder")
    admin = _register(client, "modmin")
    _role(moderator["id"], UserRole.MODERATOR)
    _role(admin["id"], UserRole.ADMIN)
    video_id = _video(client, owner["token"], "hide me", "public")
    denied = client.post(
        f"/api/v1/moderation/videos/{video_id}/hide",
        headers=_auth(viewer["token"]),
        json={"reason": "no"},
    )
    assert denied.status_code == 403
    role_denied = client.post(
        f"/api/v1/admin/users/{viewer['id']}/role",
        headers=_auth(moderator["token"]),
        json={"role": "ADMIN", "reason": "no"},
    )
    assert role_denied.status_code == 403
    playback = client.post(f"/api/v1/videos/{video_id}/playback")
    assert playback.status_code == 200, playback.text
    cookie = playback.headers["set-cookie"].split("hls_session=", 1)[1].split(";", 1)[0]
    hidden = client.post(
        f"/api/v1/moderation/videos/{video_id}/hide",
        headers=_auth(moderator["token"]),
        json={"reason": "spam"},
    )
    again = client.post(
        f"/api/v1/moderation/videos/{video_id}/hide",
        headers=_auth(moderator["token"]),
        json={"reason": "spam"},
    )
    assert hidden.status_code == 200
    assert again.status_code == 200
    master = client.get(
        f"/api/v1/videos/{video_id}/hls/master.m3u8",
        headers={"Cookie": f"hls_session={cookie}"},
    )
    assert master.status_code == 404
    session = SessionLocal()
    try:
        row = session.get(PlaybackSession, uuid.UUID(cookie))
        assert row is not None and row.revoked_at is not None
        hides = session.scalars(
            select(ModerationAction).where(
                ModerationAction.action == "hide_video",
                ModerationAction.target_id == uuid.UUID(video_id),
            )
        ).all()
        assert len(hides) == 1
        with pytest.raises(Exception):
            session.execute(
                text("DELETE FROM moderation_actions WHERE id = :id"), {"id": hides[0].id}
            )
            session.commit()
    finally:
        session.rollback()
        session.close()
    feed = client.get("/api/v1/videos", params={"limit": 50})
    assert "hide me" not in feed.text
    private_id = _video(client, owner["token"], "never reported", "private")
    blocked = client.post(
        f"/api/v1/moderation/videos/{private_id}/playback",
        headers=_auth(moderator["token"]),
    )
    assert blocked.status_code == 404
    reported = client.post(
        f"/api/v1/videos/{private_id}/reports",
        headers=_auth(viewer["token"]),
        json={"reason": "spam"},
    )
    assert reported.status_code == 404
    owner_grant = client.post(
        f"/api/v1/videos/{private_id}/grants",
        headers=_auth(owner["token"]),
        json={"handle": viewer["handle"]},
    )
    assert owner_grant.status_code == 200, owner_grant.text
    reported = client.post(
        f"/api/v1/videos/{private_id}/reports",
        headers=_auth(viewer["token"]),
        json={"reason": "spam"},
    )
    assert reported.status_code == 204, reported.text
    allowed = client.post(
        f"/api/v1/moderation/videos/{private_id}/playback",
        headers=_auth(moderator["token"]),
    )
    assert allowed.status_code == 200, allowed.text
    comment = client.post(
        f"/api/v1/videos/{video_id}/comments",
        headers=_auth(viewer["token"]),
        json={"body": "remove this"},
    )
    assert comment.status_code == 404
    visible = _video(client, owner["token"], "comment host", "public")
    comment = client.post(
        f"/api/v1/videos/{visible}/comments",
        headers=_auth(viewer["token"]),
        json={"body": "remove this"},
    )
    assert comment.status_code == 200, comment.text
    hidden_comment = client.post(
        f"/api/v1/moderation/comments/{comment.json()['id']}/hide",
        headers=_auth(moderator["token"]),
        json={"reason": "spam"},
    )
    assert hidden_comment.status_code == 204
    listed = client.get(f"/api/v1/videos/{visible}/comments")
    assert "remove this" not in listed.text
    redis_client().delete(f"rl:report:{viewer['id']}")
    for _ in range(10):
        extra = client.post(
            f"/api/v1/videos/{video_id}/reports",
            headers=_auth(viewer["token"]),
            json={"reason": "other"},
        )
        assert extra.status_code == 404
    limited = client.post(
        f"/api/v1/videos/{video_id}/reports",
        headers=_auth(viewer["token"]),
        json={"reason": "other"},
    )
    assert limited.status_code == 429
    assert limited.headers["retry-after"]


def test_deleted_comment_keeps_visible_replies(client: TestClient) -> None:
    owner = _register(client, "delown")
    stranger = _register(client, "delstr")
    video_id = _video(client, owner["token"], "comment tombstone")
    created = client.post(
        f"/api/v1/videos/{video_id}/comments",
        headers=_auth(owner["token"]),
        json={"body": "parent text"},
    )
    assert created.status_code == 200, created.text
    reply = client.post(
        f"/api/v1/videos/{video_id}/comments",
        headers=_auth(stranger["token"]),
        json={"body": "still here", "parent_id": created.json()["id"]},
    )
    assert reply.status_code == 200, reply.text
    removed = client.delete(
        f"/api/v1/comments/{created.json()['id']}",
        headers=_auth(owner["token"]),
    )
    assert removed.status_code == 204
    listed = client.get(f"/api/v1/videos/{video_id}/comments")
    assert listed.status_code == 200
    parent = listed.json()["items"][0]
    assert parent["body"] == "Comment deleted"
    assert parent["author"] is None
    assert parent["replies"][0]["body"] == "still here"
    gone = client.delete(
        f"/api/v1/comments/{reply.json()['id']}",
        headers=_auth(stranger["token"]),
    )
    assert gone.status_code == 204
    after = client.get(f"/api/v1/videos/{video_id}/comments")
    assert after.json()["items"] == []


def test_last_active_admin_cannot_be_deactivated(client: TestClient) -> None:
    extra = _register(client, "admintwo")
    _role(extra["id"], UserRole.ADMIN)
    session = SessionLocal()
    changed: list[uuid.UUID] = []
    try:
        others = session.scalars(
            select(User).where(
                User.role == UserRole.ADMIN,
                User.is_active.is_(True),
                User.id != uuid.UUID(extra["id"]),
            )
        ).all()
        for user in others:
            user.is_active = False
            changed.append(user.id)
        session.commit()
        blocked = client.post(
            f"/api/v1/admin/users/{extra['id']}/deactivation",
            headers=_auth(extra["token"]),
            json={"active": False, "reason": "last admin"},
        )
        assert blocked.status_code == 409, blocked.text
        still = session.get(User, uuid.UUID(extra["id"]))
        session.refresh(still)
        assert still is not None and still.is_active is True
    finally:
        session.rollback()
        for user_id in changed:
            user = session.get(User, user_id)
            if user is not None:
                user.is_active = True
        session.commit()
        session.close()


def test_social_schema_downgrades(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    url = make_url(settings.database_url)
    database = f"social_{uuid.uuid4().hex[:8]}"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database}"'))
    admin.dispose()
    monkeypatch.setenv(
        "DATABASE_URL", url.set(database=database).render_as_string(hide_password=False)
    )
    get_settings.cache_clear()
    upgrade_head()
    config = Config("alembic.ini")
    command.downgrade(config, "0005_playback")
    engine = create_engine(url.set(database=database))
    with engine.connect() as connection:
        names = connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        ).scalars()
        tables = set(names)
    engine.dispose()
    assert "videos" in tables
    assert "playlists" not in tables
    assert "moderation_actions" not in tables
    cleanup = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with cleanup.connect() as connection:
        connection.execute(text(f'DROP DATABASE "{database}"'))
    cleanup.dispose()
    get_settings.cache_clear()
