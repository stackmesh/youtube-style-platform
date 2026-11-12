"""Upload sessions against the Compose SeaweedFS and PostgreSQL services."""

import io
import time
import uuid

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.migrate import upgrade_head
from app.db.models import (
    JobStatus,
    ProcessingStatus,
    UploadSession,
    UserStorageQuota,
    Video,
    VideoProcessingJob,
)
from app.db.session import SessionLocal
from app.infrastructure.redis import redis_client
from app.infrastructure.storage import storage_client
from app.main import app
from app.uploads.objects import original_key
from app.uploads.service import sweep_uploads


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
    return {"token": response.json()["access_token"]}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _upload_row(session: Session, video_id: str) -> UploadSession | None:
    return session.scalar(
        select(UploadSession).where(UploadSession.video_id == uuid.UUID(video_id))
    )


def _draft(client: TestClient, token: str) -> str:
    response = client.post(
        "/api/v1/videos",
        headers=_auth(token),
        json={
            "title": "Upload",
            "description": "Kept",
            "tags": [],
            "visibility": "private",
            "object_key": "originals/chosen-by-client",
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def _initiate(
    client: TestClient,
    token: str,
    video_id: str,
    size: int,
    content_type: str = "video/mp4",
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/videos/{video_id}/upload",
        headers=_auth(token),
        json={"byte_size": size, "content_type": content_type, "object_key": "originals/evil"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _put_parts(
    client: TestClient, token: str, video_id: str, payload: bytes
) -> list[dict[str, object]]:
    session = SessionLocal()
    try:
        upload = _upload_row(session, video_id)
        assert upload is not None
        part_count = upload.part_count
    finally:
        session.close()
    completed: list[dict[str, object]] = []
    offset = 0
    for start in range(1, part_count + 1, 20):
        numbers = list(range(start, min(part_count, start + 19) + 1))
        signed = client.post(
            f"/api/v1/videos/{video_id}/upload/parts",
            headers=_auth(token),
            json={"part_numbers": numbers},
        )
        assert signed.status_code == 200, signed.text
        for part in signed.json()["parts"]:
            chunk = payload[offset : offset + part["content_length"]]
            assert len(chunk) == part["content_length"]
            stored = httpx.put(part["url"], content=chunk)
            assert stored.status_code in {200, 201}, stored.text
            offset += part["content_length"]
            completed.append({"part_number": part["part_number"], "etag": stored.headers["etag"]})
    return completed


def test_anonymous_cannot_start_an_upload(client: TestClient) -> None:
    owner = _register(client, "owner")
    video_id = _draft(client, owner["token"])
    response = client.post(
        f"/api/v1/videos/{video_id}/upload",
        json={"byte_size": 32, "content_type": "video/mp4"},
    )
    assert response.status_code == 401


def test_another_user_cannot_upload_or_finish(client: TestClient) -> None:
    owner = _register(client, "owner")
    other = _register(client, "other")
    video_id = _draft(client, owner["token"])
    _initiate(client, owner["token"], video_id, 32)
    foreign = client.post(
        f"/api/v1/videos/{video_id}/upload/parts",
        headers=_auth(other["token"]),
        json={"part_numbers": [1]},
    )
    assert foreign.status_code == 404
    finished = client.post(
        f"/api/v1/videos/{video_id}/upload/complete",
        headers=_auth(other["token"]),
        json={"parts": [{"part_number": 1, "etag": "abc"}]},
    )
    assert finished.status_code == 404
    aborted = client.post(f"/api/v1/videos/{video_id}/upload/abort", headers=_auth(other["token"]))
    assert aborted.status_code == 404


def test_file_limits_and_types(client: TestClient) -> None:
    owner = _register(client, "limits")
    video_id = _draft(client, owner["token"])
    huge = client.post(
        f"/api/v1/videos/{video_id}/upload",
        headers=_auth(owner["token"]),
        json={"byte_size": 3_000_000_000, "content_type": "video/mp4"},
    )
    assert huge.status_code == 422
    bad_type = client.post(
        f"/api/v1/videos/{video_id}/upload",
        headers=_auth(owner["token"]),
        json={"byte_size": 32, "content_type": "application/x-msdownload"},
    )
    assert bad_type.status_code == 422
    bad_parts = client.post(
        f"/api/v1/videos/{video_id}/upload/parts",
        headers=_auth(owner["token"]),
        json={"part_numbers": [0, 99]},
    )
    assert bad_parts.status_code == 404


def test_small_multipart_upload_stays_private(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    owner = _register(client, "small")
    video_id = _draft(client, owner["token"])
    payload = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
    created = _initiate(client, owner["token"], video_id, len(payload))
    assert "object_key" not in created
    session = SessionLocal()
    try:
        upload = _upload_row(session, video_id)
        assert upload is not None
        assert upload.object_key == original_key(video_id)
        assert upload.object_key != "originals/evil"
    finally:
        session.close()
    parts = _put_parts(client, owner["token"], video_id, payload)
    first = client.post(
        f"/api/v1/videos/{video_id}/upload/complete",
        headers=_auth(owner["token"]),
        json={"parts": parts},
    )
    assert first.status_code == 200, first.text
    assert first.json()["processing_status"] == "uploaded"
    second = client.post(
        f"/api/v1/videos/{video_id}/upload/complete",
        headers=_auth(owner["token"]),
        json={"parts": parts},
    )
    assert second.status_code == 200, second.text
    session = SessionLocal()
    try:
        jobs = session.scalars(
            select(VideoProcessingJob).where(VideoProcessingJob.video_id == uuid.UUID(video_id))
        ).all()
        assert len(jobs) == 1
        video = session.get(Video, uuid.UUID(video_id))
        assert video is not None
        assert video.processing_status is not ProcessingStatus.ready
        key = video.original_object_key
    finally:
        session.close()
    assert key == original_key(video_id)
    settings = get_settings()
    head = storage_client().head_object(Bucket=settings.s3_bucket, Key=key)
    assert head["ContentLength"] == len(payload)
    anonymous = httpx.get(f"{settings.s3_endpoint_url}/{settings.s3_bucket}/{key}")
    assert anonymous.status_code in {401, 403}
    application_logs = "\n".join(
        record.getMessage() for record in caplog.records if record.name.startswith("app.")
    )
    assert "X-Amz-Signature" not in application_logs
    deadline = time.time() + 45
    status = "uploaded"
    while time.time() < deadline:
        view = client.get(f"/api/v1/videos/{video_id}/processing", headers=_auth(owner["token"]))
        assert view.status_code == 200, view.text
        body = view.json()
        assert "ffmpeg" not in view.text.lower()
        status = body["processing_status"]
        if status in {"failed", "uploaded"} and body["error_code"]:
            break
        if status == "failed":
            break
        time.sleep(1)
    assert status != "ready"


def test_two_part_upload_and_abort_cleanup(client: TestClient) -> None:
    owner = _register(client, "parts")
    video_id = _draft(client, owner["token"])
    size = 16_777_216 + 32
    payload = b"\x00" * size
    _initiate(client, owner["token"], video_id, size)
    parts = _put_parts(client, owner["token"], video_id, payload)
    assert [part["part_number"] for part in parts] == [1, 2]
    mismatch = client.post(
        f"/api/v1/videos/{video_id}/upload/complete",
        headers=_auth(owner["token"]),
        json={
            "parts": [
                {"part_number": 1, "etag": "deadbeef"},
                {"part_number": 2, "etag": "deadbeef"},
            ]
        },
    )
    assert mismatch.status_code == 422
    session = SessionLocal()
    try:
        video = session.get(Video, uuid.UUID(video_id))
        assert video is not None
        assert video.processing_status is ProcessingStatus.failed
        jobs = session.scalar(
            select(func.count())
            .select_from(VideoProcessingJob)
            .where(VideoProcessingJob.video_id == video.id)
        )
        assert int(jobs or 0) == 0
    finally:
        session.close()
    second_id = _draft(client, owner["token"])
    _initiate(client, owner["token"], second_id, size)
    stored = _put_parts(client, owner["token"], second_id, payload)
    finished = client.post(
        f"/api/v1/videos/{second_id}/upload/complete",
        headers=_auth(owner["token"]),
        json={"parts": stored},
    )
    assert finished.status_code == 200, finished.text
    settings = get_settings()
    head = storage_client().head_object(Bucket=settings.s3_bucket, Key=original_key(second_id))
    assert head["ContentLength"] == size
    object_url = f"{settings.s3_endpoint_url}/{settings.s3_bucket}/{original_key(second_id)}"
    anonymous = httpx.get(object_url)
    assert anonymous.status_code in {401, 403}


def test_abort_is_repeatable(client: TestClient) -> None:
    owner = _register(client, "abort")
    video_id = _draft(client, owner["token"])
    _initiate(client, owner["token"], video_id, 128)
    first = client.post(f"/api/v1/videos/{video_id}/upload/abort", headers=_auth(owner["token"]))
    second = client.post(f"/api/v1/videos/{video_id}/upload/abort", headers=_auth(owner["token"]))
    assert first.status_code == 204
    assert second.status_code == 204
    session = SessionLocal()
    try:
        video = session.get(Video, uuid.UUID(video_id))
        assert video is not None
        channel_user = video.channel.user_id
        quota = session.get(UserStorageQuota, channel_user)
        assert quota is not None
        assert quota.bytes_reserved == 0
        assert video.processing_status is ProcessingStatus.abandoned
    finally:
        session.close()


def test_expired_session_cannot_complete(client: TestClient) -> None:
    owner = _register(client, "expire")
    video_id = _draft(client, owner["token"])
    _initiate(client, owner["token"], video_id, 64)
    session = SessionLocal()
    try:
        upload = _upload_row(session, video_id)
        assert upload is not None
        upload.expires_at = upload.created_at
        session.commit()
    finally:
        session.close()
    response = client.post(
        f"/api/v1/videos/{video_id}/upload/complete",
        headers=_auth(owner["token"]),
        json={"parts": [{"part_number": 1, "etag": "abc"}]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "UPLOAD_EXPIRED"
    session = SessionLocal()
    try:
        sweep_uploads(session)
        upload = _upload_row(session, video_id)
        video = session.get(Video, uuid.UUID(video_id))
        assert upload is not None and video is not None
        quota = session.get(UserStorageQuota, video.channel.user_id)
        assert quota is not None
        assert upload.status.value == "expired"
        assert quota.bytes_reserved == 0
    finally:
        session.close()


def test_quota_reservation_cannot_exceed_the_limit(client: TestClient) -> None:
    owner = _register(client, "quota")
    first = _draft(client, owner["token"])
    second = _draft(client, owner["token"])
    session = SessionLocal()
    try:
        video = session.get(Video, uuid.UUID(first))
        assert video is not None
        quota = session.get(UserStorageQuota, video.channel.user_id)
        assert quota is not None
        quota.bytes_limit = 1000
        session.commit()
    finally:
        session.close()
    allowed = client.post(
        f"/api/v1/videos/{first}/upload",
        headers=_auth(owner["token"]),
        json={"byte_size": 600, "content_type": "video/mp4"},
    )
    refused = client.post(
        f"/api/v1/videos/{second}/upload",
        headers=_auth(owner["token"]),
        json={"byte_size": 600, "content_type": "video/mp4"},
    )
    assert allowed.status_code == 200, allowed.text
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "QUOTA_EXCEEDED"


def test_thumbnail_rejects_text_and_stores_jpeg(client: TestClient) -> None:
    owner = _register(client, "thumb")
    video_id = _draft(client, owner["token"])
    rejected = client.put(
        f"/api/v1/videos/{video_id}/thumbnail",
        headers={**_auth(owner["token"]), "Content-Type": "image/png"},
        content=b"not-an-image",
    )
    assert rejected.status_code == 422
    source = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(source, format="PNG")
    stored = client.put(
        f"/api/v1/videos/{video_id}/thumbnail",
        headers={**_auth(owner["token"]), "Content-Type": "image/png"},
        content=source.getvalue(),
    )
    assert stored.status_code == 204, stored.text
    session = SessionLocal()
    try:
        video = session.get(Video, uuid.UUID(video_id))
        assert video is not None and video.custom_thumbnail_key
        body = storage_client().get_object(
            Bucket=get_settings().s3_bucket,
            Key=video.custom_thumbnail_key,
        )
        data = body["Body"].read()
    finally:
        session.close()
    assert data.startswith(b"\xff\xd8")
    assert b"Exif" not in data
    removed = client.delete(f"/api/v1/videos/{video_id}/thumbnail", headers=_auth(owner["token"]))
    assert removed.status_code == 204


def test_processing_retry_is_bounded(client: TestClient) -> None:
    owner = _register(client, "retry")
    video_id = _draft(client, owner["token"])
    session = SessionLocal()
    try:
        video = session.get(Video, uuid.UUID(video_id))
        assert video is not None
        video.processing_status = ProcessingStatus.failed
        video.original_object_key = original_key(video_id)
        for attempt in range(1, 7):
            session.add(
                VideoProcessingJob(
                    id=uuid.uuid4(),
                    video_id=video.id,
                    attempt=attempt,
                    status=JobStatus.failed,
                    next_run_at=video.created_at,
                )
            )
        session.commit()
    finally:
        session.close()
    response = client.post(
        f"/api/v1/videos/{video_id}/processing/retry",
        headers=_auth(owner["token"]),
    )
    assert response.status_code == 409


def test_upload_schema_downgrades(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    admin_url = make_url(settings.database_url).set(database="postgres")
    database_name = f"uploads_{uuid.uuid4().hex[:12]}"
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
                text("SELECT to_regclass('public.upload_sessions')")
            ).scalar()
            assert present == "upload_sessions"
        cfg = Config("alembic.ini")
        command.downgrade(cfg, "0003_catalogue")
        with engine.connect() as connection:
            sessions = connection.execute(text("SELECT to_regclass('public.upload_sessions')"))
            videos = connection.execute(text("SELECT to_regclass('public.videos')"))
            assert sessions.scalar() is None
            assert videos.scalar() == "videos"
        command.upgrade(cfg, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT to_regclass('public.video_processing_jobs')")
                ).scalar()
                == "video_processing_jobs"
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
