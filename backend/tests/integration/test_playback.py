"""Playback authorization and a real HLS package when ffmpeg is installed."""

import logging
import shutil
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.ids import uuid7
from app.db.migrate import upgrade_head
from app.db.models import (
    JobStatus,
    ModerationStatus,
    PlaybackSession,
    ProcessingStatus,
    Video,
    VideoProcessingJob,
)
from app.db.session import SessionLocal
from app.infrastructure.redis import redis_client
from app.main import app
from app.playback.service import maintain_playback
from app.uploads.media import execute_claimed_job
from app.uploads.objects import get_bytes, head_size, put_bytes

pytestmark = pytest.mark.integration

_MASTER = (
    "#EXTM3U\n"
    "#EXT-X-VERSION:3\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=928000,RESOLUTION=128x72\n"
    "360/index.m3u8\n"
)
_VARIANT = (
    "#EXTM3U\n"
    "#EXT-X-VERSION:3\n"
    "#EXT-X-TARGETDURATION:6\n"
    "#EXT-X-PLAYLIST-TYPE:VOD\n"
    "#EXTINF:2.0,\n"
    "segment_00001.ts\n"
    "#EXT-X-ENDLIST\n"
)


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
    }


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create(client: TestClient, token: str, title: str, visibility: str = "private") -> str:
    response = client.post(
        "/api/v1/videos",
        headers=_auth(token),
        json={"title": title, "description": "", "tags": [], "visibility": visibility},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def _install(video_id: str, *, hidden: bool = False) -> None:
    put_bytes(
        f"hls/{video_id}/master.m3u8",
        _MASTER.encode(),
        "application/vnd.apple.mpegurl",
        cache_control="no-store",
    )
    put_bytes(
        f"hls/{video_id}/360/index.m3u8",
        _VARIANT.encode(),
        "application/vnd.apple.mpegurl",
        cache_control="no-store",
    )
    put_bytes(
        f"hls/{video_id}/360/segment_00001.ts",
        b"\x47" * 188,
        "video/mp2t",
        cache_control="private, max-age=120",
    )
    put_bytes(f"thumbnails/{video_id}/auto.jpg", b"\xff\xd8\xff", "image/jpeg")
    session = SessionLocal()
    try:
        video = session.get(Video, uuid.UUID(video_id))
        assert video is not None
        video.processing_status = ProcessingStatus.ready
        video.published_at = datetime(2000, 1, 1, tzinfo=UTC)
        video.hls_master_key = f"hls/{video_id}/master.m3u8"
        video.auto_thumbnail_key = f"thumbnails/{video_id}/auto.jpg"
        video.duration_seconds = 2
        video.width = 128
        video.height = 72
        if hidden:
            video.moderation_status = ModerationStatus.hidden
        session.commit()
    finally:
        session.close()


def _cookie(response: httpx.Response) -> str:
    header = response.headers.get("set-cookie", "")
    assert "hls_session=" in header
    return header.split("hls_session=", 1)[1].split(";", 1)[0]


def _play(client: TestClient, video_id: str, headers: dict[str, str] | None = None) -> str:
    response = client.post(f"/api/v1/videos/{video_id}/playback", headers=headers)
    assert response.status_code == 200, response.text
    token = _cookie(response)
    assert uuid.UUID(token).version == 4
    assert "http://" not in response.text
    assert "originals/" not in response.text
    assert "X-Amz-" not in response.text
    return token


def _with_cookie(headers: dict[str, str] | None, token: str) -> dict[str, str]:
    merged = dict(headers or {})
    merged["Cookie"] = f"hls_session={token}"
    return merged


def _session_row(token: str) -> PlaybackSession:
    session = SessionLocal()
    try:
        row = session.get(PlaybackSession, uuid.UUID(token))
        assert row is not None
        session.expunge(row)
        return row
    finally:
        session.close()


def test_private_playback_follows_grants_and_sessions(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    owner = _register(client, "owner")
    stranger = _register(client, "stranger")
    grantee = _register(client, "grantee")
    title = f"private-{uuid.uuid4().hex}"
    video_id = _create(client, owner["token"], title)
    other_id = _create(client, owner["token"], "other")
    _install(video_id)
    _install(other_id)
    hidden = client.post(
        f"/api/v1/videos/{video_id}/playback",
        headers=_auth(stranger["token"]),
    )
    assert hidden.status_code == 404
    assert title not in hidden.text
    anonymous = client.post(f"/api/v1/videos/{video_id}/playback")
    assert anonymous.status_code == 404
    draft = _create(client, owner["token"], "not-ready-title")
    early = client.post(f"/api/v1/videos/{draft}/playback", headers=_auth(owner["token"]))
    assert early.status_code == 409
    assert early.json()["error"]["code"] == "VIDEO_NOT_READY"
    assert "not-ready-title" not in early.text

    granted = client.post(
        f"/api/v1/videos/{video_id}/grants",
        headers=_auth(owner["token"]),
        json={"handle": grantee["handle"]},
    )
    assert granted.status_code == 200, granted.text
    token = _play(client, video_id, _auth(grantee["token"]))
    refreshed = client.post(
        f"/api/v1/videos/{video_id}/playback",
        headers=_with_cookie(_auth(grantee["token"]), token),
    )
    assert refreshed.status_code == 200
    assert _cookie(refreshed) == token
    caplog.set_level(logging.INFO)
    master = client.get(
        f"/api/v1/videos/{video_id}/hls/master.m3u8",
        headers=_with_cookie(_auth(grantee["token"]), token),
    )
    assert master.status_code == 200, master.text
    assert master.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert "X-Amz-" not in master.text
    assert "360/index.m3u8" in master.text
    variant = client.get(
        f"/api/v1/videos/{video_id}/hls/360/index.m3u8",
        headers=_with_cookie(_auth(grantee["token"]), token),
    )
    assert variant.status_code == 200
    assert "segment_00001.ts" in variant.text
    assert "X-Amz-" not in variant.text
    segment = client.get(
        f"/api/v1/videos/{video_id}/hls/360/segment_00001.ts",
        headers=_with_cookie(_auth(grantee["token"]), token),
        follow_redirects=False,
    )
    assert segment.status_code == 302
    location = segment.headers["location"]
    assert location.startswith("http://s3.localhost/")
    assert f"hls/{video_id}/360/segment_00001.ts" in location
    assert not any(
        "X-Amz-Signature" in record.message
        for record in caplog.records
        if record.name.startswith("app.")
    )
    wrong = client.get(
        f"/api/v1/videos/{other_id}/hls/360/segment_00001.ts",
        headers=_with_cookie(_auth(grantee["token"]), token),
        follow_redirects=False,
    )
    assert wrong.status_code == 404
    assert _session_row(token).revoked_at is None
    for path in (
        f"/api/v1/videos/{video_id}/hls/720/segment_00001.ts",
        f"/api/v1/videos/{video_id}/hls/360/segment_99999.ts",
        f"/api/v1/videos/{video_id}/hls/360/%2e%2e%2fsource",
        f"/api/v1/videos/{video_id}/hls/originals/source",
    ):
        rejected = client.get(
            path,
            headers=_with_cookie(_auth(grantee["token"]), token),
            follow_redirects=False,
        )
        assert rejected.status_code == 404, path

    revoked = client.delete(
        f"/api/v1/videos/{video_id}/grants/{grantee['handle']}",
        headers=_auth(owner["token"]),
    )
    assert revoked.status_code == 204, revoked.text
    denied = client.get(
        f"/api/v1/videos/{video_id}/hls/master.m3u8",
        headers=_with_cookie(_auth(grantee["token"]), token),
    )
    assert denied.status_code == 404
    assert _session_row(token).revoked_at is not None

    owner_token = _play(client, video_id, _auth(owner["token"]))
    session = SessionLocal()
    try:
        row = session.get(PlaybackSession, uuid.UUID(owner_token))
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        session.commit()
    finally:
        session.close()
    expired = client.get(
        f"/api/v1/videos/{video_id}/hls/master.m3u8",
        headers=_with_cookie(_auth(owner["token"]), owner_token),
    )
    assert expired.status_code == 404
    fresh = _play(client, video_id, _auth(owner["token"]))
    client.post("/api/v1/auth/logout", headers=_auth(owner["token"]))
    assert _session_row(fresh).revoked_at is not None
    after_logout = client.get(
        f"/api/v1/videos/{video_id}/hls/360/segment_00001.ts",
        headers={"Cookie": f"hls_session={fresh}"},
        follow_redirects=False,
    )
    assert after_logout.status_code == 404

    live = _register(client, "live")
    live_id = _create(client, live["token"], "ending")
    _install(live_id)
    live_cookie = _play(client, live_id, _auth(live["token"]))
    end = client.delete(
        f"/api/v1/videos/{live_id}/playback",
        headers=_with_cookie(_auth(live["token"]), live_cookie),
    )
    assert end.status_code == 204
    assert (
        client.get(
            f"/api/v1/videos/{live_id}/hls/master.m3u8",
            headers=_with_cookie(_auth(live["token"]), live_cookie),
        ).status_code
        == 404
    )


def test_unlisted_public_hidden_and_deleted_playback(client: TestClient) -> None:
    owner = _register(client, "owner")
    unlisted = _create(client, owner["token"], "unlisted-title", "unlisted")
    _install(unlisted)
    token = _play(client, unlisted)
    assert (
        client.get(
            f"/api/v1/videos/{unlisted}/hls/master.m3u8",
            headers={"Cookie": f"hls_session={token}"},
        ).status_code
        == 200
    )
    session = SessionLocal()
    try:
        video = session.get(Video, uuid.UUID(unlisted))
        assert video is not None
        before = video.view_count
    finally:
        session.close()
    assert before == 0
    counted = client.post(
        f"/api/v1/videos/{unlisted}/views",
        headers={"Cookie": f"hls_session={token}"},
    )
    assert counted.status_code == 204
    repeat = client.post(
        f"/api/v1/videos/{unlisted}/views",
        headers={"Cookie": f"hls_session={token}"},
    )
    assert repeat.status_code == 204
    session = SessionLocal()
    try:
        video = session.get(Video, uuid.UUID(unlisted))
        assert video is not None
        assert video.view_count == 1
    finally:
        session.close()

    public = _create(client, owner["token"], "public-title", "public")
    _install(public)
    thumb = client.get(f"/api/v1/videos/{public}/thumbnail", follow_redirects=False)
    assert thumb.status_code == 302
    assert thumb.headers["location"].startswith("http://s3.localhost/")
    private_thumb = client.get(f"/api/v1/videos/{unlisted}/thumbnail")
    assert private_thumb.status_code == 404

    hidden_id = _create(client, owner["token"], "hidden-title", "public")
    _install(hidden_id)
    hidden_cookie = _play(client, hidden_id)
    session = SessionLocal()
    try:
        video = session.get(Video, uuid.UUID(hidden_id))
        assert video is not None
        video.moderation_status = ModerationStatus.hidden
        session.commit()
    finally:
        session.close()
    hidden = client.get(
        f"/api/v1/videos/{hidden_id}/hls/360/segment_00001.ts",
        headers={"Cookie": f"hls_session={hidden_cookie}"},
        follow_redirects=False,
    )
    assert hidden.status_code == 404
    assert "hidden-title" not in hidden.text
    assert _session_row(hidden_cookie).revoked_at is not None

    deleted = _create(client, owner["token"], "deleted-title", "unlisted")
    _install(deleted)
    deleted_cookie = _play(client, deleted)
    assert (
        client.delete(
            f"/api/v1/videos/{deleted}",
            headers=_auth(owner["token"]),
        ).status_code
        == 204
    )
    assert (
        client.get(
            f"/api/v1/videos/{deleted}/hls/master.m3u8",
            headers={"Cookie": f"hls_session={deleted_cookie}"},
        ).status_code
        == 404
    )
    maintain = SessionLocal()
    try:
        maintain_playback(maintain)
    finally:
        maintain.close()
    assert head_size(f"hls/{deleted}/master.m3u8") is None
    session = SessionLocal()
    try:
        video = session.get(Video, uuid.UUID(deleted))
        assert video is not None
        assert video.storage_released_at is not None
    finally:
        session.close()

    settings = get_settings()
    unsigned = httpx.get(
        f"{settings.s3_endpoint_url}/{settings.s3_bucket}/hls/{public}/master.m3u8",
        timeout=10,
    )
    assert unsigned.status_code == 403


def test_malformed_input_never_becomes_ready(client: TestClient) -> None:
    owner = _register(client, "owner")
    video_id = _create(client, owner["token"], "broken")
    put_bytes(f"originals/{video_id}/source", b"not-a-video", "video/mp4")
    session = SessionLocal()
    try:
        video = session.get(Video, uuid.UUID(video_id))
        assert video is not None
        video.processing_status = ProcessingStatus.uploaded
        video.original_object_key = f"originals/{video_id}/source"
        video.source_byte_size = 11
        session.add(
            VideoProcessingJob(
                id=uuid7(),
                video_id=video.id,
                attempt=1,
                status=JobStatus.queued,
                next_run_at=datetime.now(UTC),
            )
        )
        session.commit()
    finally:
        session.close()
    _finish(video_id)
    session = SessionLocal()
    try:
        video = session.get(Video, uuid.UUID(video_id))
        assert video is not None
        assert video.processing_status is not ProcessingStatus.ready
        assert video.hls_master_key is None
    finally:
        session.close()


def test_real_hls_is_playable_and_idempotent(client: TestClient) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg is not installed")
    source = Path_tmp()
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=128x72:rate=10:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
        timeout=30,
    )
    owner = _register(client, "owner")
    video_id = _create(client, owner["token"], "tiny-real")
    body = source.read_bytes()
    put_bytes(f"originals/{video_id}/source", body, "video/mp4")
    session = SessionLocal()
    try:
        video = session.get(Video, uuid.UUID(video_id))
        assert video is not None
        video.processing_status = ProcessingStatus.uploaded
        video.original_object_key = f"originals/{video_id}/source"
        video.source_byte_size = len(body)
        session.add(
            VideoProcessingJob(
                id=uuid7(),
                video_id=video.id,
                attempt=1,
                status=JobStatus.queued,
                next_run_at=datetime.now(UTC),
            )
        )
        session.commit()
    finally:
        session.close()
    _finish(video_id)
    session = SessionLocal()
    try:
        video = session.get(Video, uuid.UUID(video_id))
        assert video is not None
        assert video.processing_status is ProcessingStatus.ready
        assert video.published_at is not None
        assert video.duration_seconds == 2
        assert video.width == 128
        assert video.height == 72
        assert video.hls_master_key == f"hls/{video_id}/master.m3u8"
        master = get_bytes(video.hls_master_key)
        assert master is not None
        text = master.decode()
        assert "RESOLUTION=128x72" in text
        assert "720/index.m3u8" not in text
        assert "1080/index.m3u8" not in text
        variant = get_bytes(f"hls/{video_id}/360/index.m3u8")
        assert variant is not None
        for line in variant.decode().splitlines():
            if line.startswith("segment_"):
                assert head_size(f"hls/{video_id}/360/{line}") is not None
    finally:
        session.close()
    token = _play(client, video_id, _auth(owner["token"]))
    master_response = client.get(
        f"/api/v1/videos/{video_id}/hls/master.m3u8",
        headers=_with_cookie(_auth(owner["token"]), token),
    )
    assert master_response.status_code == 200
    assert "X-Amz-" not in master_response.text
    anonymous = client.get(f"/api/v1/videos/{video_id}/hls/master.m3u8")
    assert anonymous.status_code == 404
    session = SessionLocal()
    try:
        video = session.get(Video, uuid.UUID(video_id))
        assert video is not None
        session.add(
            VideoProcessingJob(
                id=uuid7(),
                video_id=video.id,
                attempt=2,
                status=JobStatus.queued,
                next_run_at=datetime.now(UTC),
            )
        )
        session.commit()
    finally:
        session.close()
    _finish(video_id)
    assert get_bytes(f"hls/{video_id}/master.m3u8") is not None
    session = SessionLocal()
    try:
        rows = session.scalars(select(Video).where(Video.id == uuid.UUID(video_id))).all()
        assert len(rows) == 1
        assert rows[0].processing_status is ProcessingStatus.ready
        assert rows[0].hls_master_key == f"hls/{video_id}/master.m3u8"
    finally:
        session.close()


def test_playback_schema_downgrades(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    admin_url = make_url(settings.database_url).set(database="postgres")
    database_name = f"playback_{uuid.uuid4().hex[:12]}"
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
            present = connection.execute(
                text("SELECT to_regclass('public.playback_sessions')")
            ).scalar()
            assert present == "playback_sessions"
        cfg = Config("alembic.ini")
        command.downgrade(cfg, "0004_uploads")
        with engine.connect() as connection:
            sessions = connection.execute(text("SELECT to_regclass('public.playback_sessions')"))
            videos = connection.execute(text("SELECT to_regclass('public.videos')"))
            assert sessions.scalar() is None
            assert videos.scalar() == "videos"
    finally:
        engine.dispose()
        get_settings.cache_clear()
        cleanup = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with cleanup.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        cleanup.dispose()


def _finish(video_id: str) -> None:
    """Run this video's queued job without claiming another video's work."""
    deadline = time.time() + 90
    while time.time() < deadline:
        session = SessionLocal()
        job_id: uuid.UUID | None = None
        try:
            job = session.scalar(
                select(VideoProcessingJob)
                .where(
                    VideoProcessingJob.video_id == uuid.UUID(video_id),
                    VideoProcessingJob.status == JobStatus.queued,
                )
                .order_by(VideoProcessingJob.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is not None:
                now = datetime.now(UTC)
                job.status = JobStatus.running
                job.locked_by = "playback-test"
                job.locked_until = now + timedelta(hours=1)
                job.started_at = now
                video = session.get(Video, job.video_id)
                if video is not None:
                    video.processing_status = ProcessingStatus.processing
                session.commit()
                job_id = job.id
            else:
                video = session.get(Video, uuid.UUID(video_id))
                finished = video is not None and video.processing_status in {
                    ProcessingStatus.ready,
                    ProcessingStatus.failed,
                }
                session.rollback()
                if finished:
                    return
        finally:
            session.close()
        if job_id is not None:
            session = SessionLocal()
            try:
                execute_claimed_job(session, job_id)
            finally:
                session.close()
            return
        time.sleep(0.5)
    raise AssertionError("processing did not finish")


def Path_tmp():
    from pathlib import Path

    return Path("/tmp") / f"playback-{uuid.uuid4().hex}.mp4"
