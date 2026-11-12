import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import Executable

from app.catalogue.cursors import encode_time_id
from app.catalogue.service import feed_select
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.db.migrate import upgrade_head
from app.db.models import (
    ModerationStatus,
    ProcessingStatus,
    User,
    Video,
    VideoAccessGrant,
    Visibility,
)
from app.db.session import SessionLocal
from app.infrastructure.redis import redis_client
from app.main import app
from app.search.service import video_search_select


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
        "handle": body["user"]["channel"]["handle"],
        "email": body["user"]["email"],
    }


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create(client: TestClient, token: str, title: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": title,
        "description": extra.pop("description", ""),
        "tags": extra.pop("tags", []),
        "visibility": extra.pop("visibility", "private"),
        "channel_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
    }
    response = client.post("/api/v1/videos", headers=_auth(token), json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _mark_ready(
    video_id: str,
    when: datetime,
    *,
    visibility: Visibility | None = None,
    views: int | None = None,
) -> None:
    session = SessionLocal()
    try:
        video = session.get(Video, uuid.UUID(video_id))
        assert video is not None
        video.processing_status = ProcessingStatus.ready
        video.published_at = when
        if visibility is not None:
            video.visibility = visibility
        if views is not None:
            video.view_count = views
        session.commit()
    finally:
        session.close()


def _hide(video_id: str) -> None:
    session = SessionLocal()
    try:
        video = session.get(Video, uuid.UUID(video_id))
        assert video is not None
        video.moderation_status = ModerationStatus.hidden
        session.commit()
    finally:
        session.close()


def test_owner_creates_and_edits_only_their_own_video(client: TestClient) -> None:
    owner = _register(client, "owner")
    other = _register(client, "other")
    created = _create(
        client,
        owner["token"],
        "  Studio notes  ",
        description="First cut",
        tags=["Travel", "travel"],
        visibility="public",
    )
    assert created["title"] == "Studio notes"
    assert created["tags"] == ["travel"]
    assert created["visibility"] == "public"
    assert created["processing_status"] == "draft"
    assert created["channel"]["handle"] == owner["handle"]
    assert "thumbnail_url" not in created
    assert "duration_seconds" not in created

    video_id = str(created["id"])
    renamed = client.patch(
        f"/api/v1/videos/{video_id}",
        headers=_auth(owner["token"]),
        json={"title": "Studio notes two", "visibility": "unlisted"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["visibility"] == "unlisted"
    session = SessionLocal()
    try:
        stored = session.get(Video, uuid.UUID(video_id))
        assert stored is not None
        assert stored.acl_version == 2
        grant = VideoAccessGrant(
            id=uuid.uuid4(),
            video_id=stored.id,
            grantee_user_id=session.scalar(select(User.id).where(User.email == other["email"])),
            granted_by_user_id=session.scalar(select(User.id).where(User.email == owner["email"])),
        )
        session.add(grant)
        session.commit()
        version = stored.acl_version
    finally:
        session.close()
    again = client.patch(
        f"/api/v1/videos/{video_id}",
        headers=_auth(owner["token"]),
        json={"description": "Still unlisted"},
    )
    assert again.status_code == 200
    session = SessionLocal()
    try:
        stored = session.get(Video, uuid.UUID(video_id))
        assert stored is not None
        assert stored.acl_version == version
        assert session.scalar(
            select(VideoAccessGrant.id).where(VideoAccessGrant.video_id == stored.id)
        )
    finally:
        session.close()

    denied = client.patch(
        f"/api/v1/videos/{video_id}",
        headers=_auth(other["token"]),
        json={"title": "Stolen"},
    )
    assert denied.status_code == 404
    assert "Studio notes" not in denied.text
    missing = client.delete(f"/api/v1/videos/{video_id}", headers=_auth(other["token"]))
    assert missing.status_code == 404
    removed = client.delete(f"/api/v1/videos/{video_id}", headers=_auth(owner["token"]))
    assert removed.status_code == 204
    gone = client.get(f"/api/v1/videos/{video_id}", headers=_auth(owner["token"]))
    assert gone.status_code == 404


def test_discovery_hides_drafts_unlisted_and_private_videos(client: TestClient) -> None:
    owner = _register(client, "studio")
    stranger = _register(client, "guest")
    grantee = _register(client, "friend")
    token = uuid.uuid4().hex[:10]
    published = datetime(2500, 1, 1, tzinfo=UTC) + timedelta(seconds=uuid.uuid4().int % 10_000_000)
    public = _create(client, owner["token"], f"{token} reel", description=f"A kelp{token} forest")
    unlisted = _create(client, owner["token"], f"{token} notes", visibility="unlisted")
    private = _create(client, owner["token"], f"{token} private cut", visibility="private")
    _create(client, owner["token"], f"{token} draft", visibility="public")
    _mark_ready(str(public["id"]), published, visibility=Visibility.public)
    _mark_ready(str(unlisted["id"]), published, visibility=Visibility.unlisted)
    _mark_ready(str(private["id"]), published, visibility=Visibility.private)

    feed = client.get(
        "/api/v1/videos",
        params={
            "limit": 10,
            "cursor": encode_time_id(
                "feed",
                published + timedelta(microseconds=1),
                uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
            ),
        },
    )
    assert feed.status_code == 200, feed.text
    titles = [item["title"] for item in feed.json()["items"]]
    assert f"{token} reel" in titles
    assert f"{token} notes" not in titles
    assert f"{token} private cut" not in titles
    assert f"{token} draft" not in titles

    found = client.get("/api/v1/search", params={"q": token, "type": "video"})
    assert found.status_code == 200, found.text
    found_titles = [item["title"] for item in found.json()["videos"]["items"]]
    assert found_titles == [f"{token} reel"]
    described = client.get("/api/v1/search", params={"q": f"kelp{token}", "type": "video"})
    assert [item["title"] for item in described.json()["videos"]["items"]] == [f"{token} reel"]

    channel = client.get(f"/api/v1/channels/{owner['handle']}/videos")
    assert [item["title"] for item in channel.json()["items"]] == [f"{token} reel"]
    own_channel = client.get(
        f"/api/v1/channels/{owner['handle']}/videos",
        headers=_auth(owner["token"]),
    )
    own_titles = {item["title"] for item in own_channel.json()["items"]}
    assert f"{token} draft" in own_titles
    assert own_channel.json()["items"][0]["processing_status"]

    direct = client.get(f"/api/v1/videos/{unlisted['id']}")
    assert direct.status_code == 200
    assert direct.json()["title"] == f"{token} notes"
    assert "processing_status" not in direct.json()

    hidden = client.get(f"/api/v1/videos/{private['id']}")
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "VIDEO_NOT_FOUND"
    assert token not in hidden.text
    stranger_view = client.get(f"/api/v1/videos/{private['id']}", headers=_auth(stranger["token"]))
    assert stranger_view.status_code == 404
    assert token not in stranger_view.text

    owner_view = client.get(f"/api/v1/videos/{private['id']}", headers=_auth(owner["token"]))
    assert owner_view.status_code == 200
    assert owner_view.json()["processing_status"] == "ready"

    granted = client.post(
        f"/api/v1/videos/{private['id']}/grants",
        headers=_auth(owner["token"]),
        json={"handle": grantee["handle"].upper()},
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["handle"] == grantee["handle"]
    assert "email" not in granted.text
    duplicate = client.post(
        f"/api/v1/videos/{private['id']}/grants",
        headers=_auth(owner["token"]),
        json={"handle": grantee["handle"]},
    )
    assert duplicate.status_code == 409
    self_grant = client.post(
        f"/api/v1/videos/{private['id']}/grants",
        headers=_auth(owner["token"]),
        json={"handle": owner["handle"]},
    )
    assert self_grant.status_code == 409
    missing_handle = client.post(
        f"/api/v1/videos/{private['id']}/grants",
        headers=_auth(owner["token"]),
        json={"handle": "missing_handle"},
    )
    assert missing_handle.status_code == 404
    assert missing_handle.json()["error"]["code"] == "CHANNEL_NOT_FOUND"

    allowed = client.get(f"/api/v1/videos/{private['id']}", headers=_auth(grantee["token"]))
    assert allowed.status_code == 200
    assert allowed.json()["title"] == f"{token} private cut"
    stranger_grants = client.get(
        f"/api/v1/videos/{private['id']}/grants",
        headers=_auth(stranger["token"]),
    )
    assert stranger_grants.status_code == 404
    assert token not in stranger_grants.text
    listed = client.get(f"/api/v1/videos/{private['id']}/grants", headers=_auth(owner["token"]))
    assert listed.status_code == 200
    assert listed.json()["items"][0]["handle"] == grantee["handle"]

    revoked = client.delete(
        f"/api/v1/videos/{private['id']}/grants/{grantee['handle']}",
        headers=_auth(owner["token"]),
    )
    assert revoked.status_code == 204
    after = client.get(f"/api/v1/videos/{private['id']}", headers=_auth(grantee["token"]))
    assert after.status_code == 404
    assert token not in after.text
    session = SessionLocal()
    try:
        stored = session.get(Video, uuid.UUID(str(private["id"])))
        assert stored is not None
        assert stored.acl_version == 3
        grant = session.scalar(
            select(VideoAccessGrant).where(VideoAccessGrant.video_id == stored.id)
        )
        assert grant is not None
        assert grant.revoked_at is not None
    finally:
        session.close()


def test_related_videos_skip_unlisted_and_private(client: TestClient) -> None:
    owner = _register(client, "relate")
    tag = f"t{uuid.uuid4().hex[:8]}"
    when = datetime(2026, 4, 1, tzinfo=UTC)
    source = _create(client, owner["token"], f"{tag} source", tags=[tag])
    tagged = _create(client, owner["token"], f"{tag} tagged", tags=[tag])
    same_channel = _create(client, owner["token"], f"{tag} channel")
    secret = _create(client, owner["token"], f"{tag} secret", tags=[tag], visibility="unlisted")
    for video, visibility in (
        (source, Visibility.public),
        (tagged, Visibility.public),
        (same_channel, Visibility.public),
        (secret, Visibility.unlisted),
    ):
        _mark_ready(str(video["id"]), when, visibility=visibility)
    related = client.get(f"/api/v1/videos/{source['id']}/related")
    assert related.status_code == 200, related.text
    titles = [item["title"] for item in related.json()["items"]]
    assert titles[0] == f"{tag} tagged"
    assert f"{tag} channel" in titles
    assert f"{tag} secret" not in titles
    assert f"{tag} source" not in titles


def test_hidden_video_is_absent_from_discovery(client: TestClient) -> None:
    owner = _register(client, "mod")
    token = uuid.uuid4().hex[:10]
    tag = f"h{uuid.uuid4().hex[:8]}"
    published = datetime(2600, 1, 1, tzinfo=UTC) + timedelta(seconds=uuid.uuid4().int % 10_000_000)
    source = _create(client, owner["token"], f"{token} source", tags=[tag], visibility="public")
    shown = _create(client, owner["token"], f"{token} shown", tags=[tag], visibility="public")
    concealed = _create(
        client, owner["token"], f"{token} concealed", tags=[tag], visibility="public"
    )
    for video in (source, shown, concealed):
        _mark_ready(str(video["id"]), published, visibility=Visibility.public)
    _hide(str(concealed["id"]))

    feed = client.get(
        "/api/v1/videos",
        params={
            "limit": 10,
            "cursor": encode_time_id(
                "feed",
                published + timedelta(microseconds=1),
                uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
            ),
        },
    )
    assert feed.status_code == 200, feed.text
    feed_titles = [item["title"] for item in feed.json()["items"]]
    assert f"{token} shown" in feed_titles
    assert f"{token} concealed" not in feed_titles

    found = client.get("/api/v1/search", params={"q": token, "type": "video"})
    assert found.status_code == 200, found.text
    found_titles = [item["title"] for item in found.json()["videos"]["items"]]
    assert f"{token} shown" in found_titles
    assert f"{token} concealed" not in found_titles

    channel = client.get(f"/api/v1/channels/{owner['handle']}/videos")
    assert channel.status_code == 200, channel.text
    channel_titles = [item["title"] for item in channel.json()["items"]]
    assert f"{token} shown" in channel_titles
    assert f"{token} concealed" not in channel_titles

    related = client.get(f"/api/v1/videos/{source['id']}/related")
    assert related.status_code == 200, related.text
    related_titles = [item["title"] for item in related.json()["items"]]
    assert f"{token} shown" in related_titles
    assert f"{token} concealed" not in related_titles

    anonymous = client.get(f"/api/v1/videos/{concealed['id']}")
    assert anonymous.status_code == 404
    assert anonymous.json()["error"]["code"] == "VIDEO_NOT_FOUND"
    assert token not in anonymous.text

    owner_view = client.get(f"/api/v1/videos/{concealed['id']}", headers=_auth(owner["token"]))
    assert owner_view.status_code == 200, owner_view.text
    assert owner_view.json()["title"] == f"{token} concealed"
    assert owner_view.json()["moderation_status"] == "hidden"
    owned = client.get("/api/v1/me/videos", headers=_auth(owner["token"]))
    assert owned.status_code == 200, owned.text
    owned_row = next(item for item in owned.json()["items"] if item["id"] == concealed["id"])
    assert owned_row["moderation_status"] == "hidden"

    blocked = client.patch(
        f"/api/v1/videos/{shown['id']}",
        headers=_auth(owner["token"]),
        json={"moderation_status": "hidden", "description": "owner cannot hide"},
    )
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["moderation_status"] == "visible"
    kept = client.patch(
        f"/api/v1/videos/{concealed['id']}",
        headers=_auth(owner["token"]),
        json={"moderation_status": "visible"},
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["moderation_status"] == "hidden"


def test_feed_cursor_does_not_skip_or_repeat(client: TestClient) -> None:
    owner = _register(client, "pages")
    base = datetime(2200, 1, 1, tzinfo=UTC) + timedelta(days=uuid.uuid4().int % 20000)
    newest = _create(client, owner["token"], "Cursor newest", visibility="public")
    middle = _create(client, owner["token"], "Cursor middle", visibility="public")
    oldest = _create(client, owner["token"], "Cursor oldest", visibility="public")
    _mark_ready(str(newest["id"]), base + timedelta(hours=3))
    _mark_ready(str(middle["id"]), base + timedelta(hours=1))
    _mark_ready(str(oldest["id"]), base)
    cursor = encode_time_id("feed", base + timedelta(hours=3), uuid.UUID(str(newest["id"])))
    inserted = _create(client, owner["token"], "Cursor between", visibility="public")
    _mark_ready(str(inserted["id"]), base + timedelta(hours=2))
    second = client.get("/api/v1/videos", params={"limit": 2, "cursor": cursor})
    assert second.status_code == 200, second.text
    titles = [item["title"] for item in second.json()["items"]]
    assert titles == ["Cursor between", "Cursor middle"]
    assert newest["id"] not in {item["id"] for item in second.json()["items"]}
    assert client.get("/api/v1/videos", params={"cursor": "%%%"}).status_code == 400
    assert client.get("/api/v1/videos", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/videos", params={"limit": 51}).status_code == 422
    empty = client.get("/api/v1/search", params={"q": "no-such-catalogue-word", "type": "video"})
    assert empty.status_code == 200
    assert empty.json()["videos"]["items"] == []
    assert client.get("/api/v1/search", params={"q": "   "}).status_code == 400


def test_search_sort_is_deterministic(client: TestClient) -> None:
    owner = _register(client, "sorts")
    token = uuid.uuid4().hex[:12]
    when = datetime(2026, 6, 1, tzinfo=UTC)
    older = _create(client, owner["token"], f"{token} alpha", visibility="public")
    newer = _create(client, owner["token"], f"{token} beta", visibility="public")
    _mark_ready(str(older["id"]), when, views=9)
    _mark_ready(str(newer["id"]), when + timedelta(days=1), views=1)
    newest = client.get("/api/v1/search", params={"q": token, "type": "video", "sort": "newest"})
    views = client.get("/api/v1/search", params={"q": token, "type": "video", "sort": "views"})
    assert [item["id"] for item in newest.json()["videos"]["items"]] == [newer["id"], older["id"]]
    assert [item["id"] for item in views.json()["videos"]["items"]] == [older["id"], newer["id"]]
    tie = uuid.uuid4().hex[:12]
    first_tie = _create(client, owner["token"], f"{tie} one", visibility="public")
    second_tie = _create(client, owner["token"], f"{tie} two", visibility="public")
    _mark_ready(str(first_tie["id"]), when + timedelta(days=2))
    _mark_ready(str(second_tie["id"]), when + timedelta(days=2))
    first = client.get("/api/v1/search", params={"q": tie, "type": "video", "sort": "newest"})
    second = client.get("/api/v1/search", params={"q": tie, "type": "video", "sort": "newest"})
    expected = [second_tie["id"], first_tie["id"]]
    assert [item["id"] for item in first.json()["videos"]["items"]] == expected
    assert [item["id"] for item in second.json()["videos"]["items"]] == expected


def test_create_video_is_rate_limited(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    owner = _register(client, "limit")

    def blocked(*_args: object, **_kwargs: object) -> None:
        error = AppError("RATE_LIMITED", "Too many requests.", 429)
        error.headers["Retry-After"] = "3600"
        raise error

    monkeypatch.setattr("app.api.videos.enforce_rate_limit", blocked)
    response = client.post(
        "/api/v1/videos",
        headers=_auth(owner["token"]),
        json={"title": "Limited"},
    )
    assert response.status_code == 429
    assert response.headers["retry-after"] == "3600"


def _explain(session: Session, statement: Executable) -> str:
    compiled = statement.compile(session.get_bind())
    rows = session.connection().exec_driver_sql("EXPLAIN " + compiled.string, compiled.params)
    return "\n".join(str(row[0]) for row in rows)


def test_feed_and_search_queries_use_their_indexes() -> None:
    session = SessionLocal()
    try:
        session.execute(text("SET LOCAL enable_seqscan = off"))
        feed_plan = _explain(session, feed_select(None).limit(24))
        session.execute(text("SET LOCAL enable_indexscan = off"))
        search_plan = _explain(session, video_search_select("studio", "relevance", None).limit(24))
        assert "videos_feed_idx" in feed_plan
        assert "videos_feed_idx" in search_plan
        assert "@@" in search_plan
        gin_plan = "\n".join(
            str(row[0])
            for row in session.execute(
                text(
                    "EXPLAIN SELECT id FROM videos "
                    "WHERE search_vector @@ plainto_tsquery('simple', 'studio')"
                )
            )
        )
        assert "videos_search_idx" in gin_plan
        session.rollback()
    finally:
        session.close()


def test_catalogue_schema_downgrades(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    admin_url = make_url(settings.database_url).set(database="postgres")
    database_name = f"catalogue_{uuid.uuid4().hex[:12]}"
    maintenance = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maintenance.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    maintenance.dispose()
    empty_url = admin_url.set(database=database_name)
    monkeypatch.setenv("DATABASE_URL", empty_url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    engine = create_engine(empty_url)
    try:
        upgrade_head()
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT to_regclass('public.videos')")).scalar() == "videos"
            )
        cfg = Config("alembic.ini")
        command.downgrade(cfg, "0002_identity")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT to_regclass('public.videos')")).scalar() is None
            assert (
                connection.execute(text("SELECT to_regclass('public.users')")).scalar() == "users"
            )
        command.upgrade(cfg, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT to_regclass('public.video_access_grants')")
                ).scalar()
                == "video_access_grants"
            )
    finally:
        engine.dispose()
        get_settings.cache_clear()
        monkeypatch.undo()
        get_settings.cache_clear()
        cleanup = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with cleanup.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        cleanup.dispose()
