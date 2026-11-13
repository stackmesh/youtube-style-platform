"""Multipart upload sessions. The browser sends bytes only to object storage."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.catalogue.acl import video_not_found
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.ids import uuid7
from app.db.models import (
    Channel,
    JobStatus,
    ProcessingStatus,
    UploadSession,
    UploadStatus,
    User,
    UserStorageQuota,
    Video,
    VideoProcessingJob,
)
from app.uploads.objects import (
    StorageError,
    StoredPart,
    abort_multipart,
    complete_multipart,
    create_multipart,
    delete_object,
    head_size,
    list_orphan_uploads,
    list_parts,
    original_key,
    presign_part,
    storage_reachable,
)

ALLOWED_CONTENT_TYPES = frozenset(
    {
        "video/mp4",
        "video/quicktime",
        "video/webm",
        "video/x-matroska",
        "video/mpeg",
    }
)
_PART_BATCH_LIMIT = 20
_SESSION_HOURS = 24
_LOCK_WINDOW = timedelta(minutes=2)
_AUTOMATIC_ATTEMPTS = 3
_MAX_JOBS = 6


def part_count_for(byte_size: int, part_size: int) -> int:
    return (byte_size + part_size - 1) // part_size


def part_length(byte_size: int, part_size: int, part_count: int, number: int) -> int:
    if number < part_count:
        return part_size
    return byte_size - (part_count - 1) * part_size


def normalize_etag(value: str) -> str:
    return value.strip().strip('"').lower()


def initiate_upload(
    session: Session,
    owner: User,
    video_id: uuid.UUID,
    byte_size: int,
    content_type: str,
) -> UploadSession:
    settings = get_settings()
    video, _channel = _owned_video(session, owner, video_id)
    if video.processing_status is not ProcessingStatus.draft:
        _fail("INVALID_STATE", "This video is not accepting an upload.", 409)
    declared = content_type.split(";", 1)[0].strip().lower()
    if declared not in ALLOWED_CONTENT_TYPES:
        _fail("VALIDATION_ERROR", "This file type cannot be uploaded.", 422)
    if byte_size < 1 or byte_size > settings.max_upload_bytes:
        _fail("VALIDATION_ERROR", "The file size is outside the upload limit.", 422)
    part_size = settings.part_size_bytes
    count = part_count_for(byte_size, part_size)
    if count < 1 or count > settings.max_parts:
        _fail("VALIDATION_ERROR", "The file needs more parts than the upload allows.", 422)
    quota = _lock_quota(session, owner.id)
    active = _active_upload_count(session, owner.id)
    if active >= settings.max_active_uploads:
        _fail("INVALID_STATE", "Too many uploads are already in progress.", 409)
    if quota.bytes_committed + quota.bytes_reserved + byte_size > quota.bytes_limit:
        _fail("QUOTA_EXCEEDED", "This upload would exceed the storage quota.", 409)
    upload = UploadSession(
        id=uuid7(),
        video_id=video.id,
        object_key=original_key(str(video.id)),
        expected_size=byte_size,
        part_size=part_size,
        part_count=count,
        status=UploadStatus.initiated,
        expires_at=datetime.now(UTC) + timedelta(hours=_SESSION_HOURS),
    )
    quota.bytes_reserved += byte_size
    video.source_content_type = declared
    session.add(upload)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        _fail("INVALID_STATE", "This video already has an upload in progress.", 409)
    try:
        upload_id = create_multipart(upload.object_key, declared)
    except StorageError:
        _release_failed_create(session, upload.id)
        _fail("STORAGE_UNAVAILABLE", "Object storage could not start the upload.", 503)
    stored = session.get(UploadSession, upload.id)
    current = session.get(Video, video.id)
    if stored is None or current is None:
        _fail("STORAGE_UNAVAILABLE", "Object storage could not start the upload.", 503)
    stored.s3_upload_id = upload_id
    current.processing_status = ProcessingStatus.uploading
    session.commit()
    session.refresh(stored)
    return stored


def presign_parts(
    session: Session,
    owner: User,
    video_id: uuid.UUID,
    part_numbers: list[int],
) -> list[dict[str, object]]:
    upload = _active_session(session, owner, video_id)
    _reject_expired(upload)
    if upload.status is not UploadStatus.initiated or upload.s3_upload_id is None:
        _fail("INVALID_STATE", "This upload is not accepting parts.", 409)
    numbers_ok = part_numbers and len(part_numbers) <= _PART_BATCH_LIMIT
    if not numbers_ok or len(set(part_numbers)) != len(part_numbers):
        _fail("UPLOAD_PART_INVALID", "The part list is not valid for this upload.", 422)
    outside = any(number < 1 or number > upload.part_count for number in part_numbers)
    if outside:
        _fail("UPLOAD_PART_INVALID", "The part list is not valid for this upload.", 422)
    if not storage_reachable():
        _fail("STORAGE_UNAVAILABLE", "Object storage is unavailable.", 503)
    expires_at = datetime.now(UTC) + timedelta(seconds=get_settings().upload_part_url_ttl_seconds)
    parts: list[dict[str, object]] = []
    for number in part_numbers:
        length = part_length(upload.expected_size, upload.part_size, upload.part_count, number)
        try:
            url = presign_part(upload.object_key, upload.s3_upload_id, number, length)
        except StorageError:
            _fail("STORAGE_UNAVAILABLE", "Object storage is unavailable.", 503)
        parts.append(
            {
                "part_number": number,
                "url": url,
                "content_length": length,
                "expires_at": expires_at,
            }
        )
    return parts


def complete_upload(
    session: Session,
    owner: User,
    video_id: uuid.UUID,
    client_parts: list[tuple[int, str]],
) -> Video:
    upload, video, _quota = _lock_session(session, owner, video_id)
    if upload.status is UploadStatus.completed:
        session.rollback()
        return _reload_video(session, video.id)
    if upload.status is UploadStatus.expired:
        session.rollback()
        _fail("UPLOAD_EXPIRED", "This upload has expired.", 409)
    if upload.status is UploadStatus.aborted:
        session.rollback()
        _fail("INVALID_STATE", "This upload was aborted.", 409)
    if upload.expires_at <= datetime.now(UTC):
        session.rollback()
        _fail("UPLOAD_EXPIRED", "This upload has expired.", 409)
    if upload.status is UploadStatus.completing and _lock_is_fresh(upload):
        session.rollback()
        error = AppError("UPLOAD_IN_PROGRESS", "This upload is already being completed.", 409)
        error.headers["Retry-After"] = "5"
        raise error
    if upload.s3_upload_id is None:
        session.rollback()
        _fail("INVALID_STATE", "This upload is not accepting parts.", 409)
    token = uuid7()
    upload.status = UploadStatus.completing
    upload.completion_token = token
    upload.completion_started_at = datetime.now(UTC)
    upload.error_code = None
    session.commit()

    try:
        stored = list_parts(upload.object_key, upload.s3_upload_id)
    except StorageError:
        _return_to_initiated(session, upload.id, token)
        _fail("STORAGE_UNAVAILABLE", "Object storage could not list the uploaded parts.", 503)
    try:
        _require_parts(upload, stored, client_parts)
    except _Rejected:
        _reject(session, upload.id, token)
        _fail("UPLOAD_REJECTED", "The uploaded parts do not match this session.", 422)
    try:
        complete_multipart(upload.object_key, upload.s3_upload_id, stored)
    except StorageError:
        try:
            size = head_size(upload.object_key)
        except StorageError:
            _fail("STORAGE_UNAVAILABLE", "Object storage could not finish the upload.", 503)
        if size == upload.expected_size:
            _finish(session, upload.id, token)
            return _reload_video(session, video.id)
        _return_to_initiated(session, upload.id, token)
        _fail("STORAGE_UNAVAILABLE", "Object storage could not finish the upload.", 503)
    try:
        size = head_size(upload.object_key)
    except StorageError:
        _fail("STORAGE_UNAVAILABLE", "Object storage could not finish the upload.", 503)
    if size != upload.expected_size:
        _reject(session, upload.id, token)
        _fail("UPLOAD_REJECTED", "The stored object does not match the declared size.", 422)
    _finish(session, upload.id, token)
    return _reload_video(session, video.id)


def abort_upload(session: Session, owner: User, video_id: uuid.UUID) -> None:
    upload, _video, _quota = _lock_session(session, owner, video_id)
    if upload.status in {UploadStatus.aborted, UploadStatus.expired, UploadStatus.completed}:
        session.rollback()
        return
    upload_id = upload.s3_upload_id
    key = upload.object_key
    session.commit()
    try:
        if upload_id is not None:
            abort_multipart(key, upload_id)
        if head_size(key) is not None:
            delete_object(key)
    except StorageError:
        _fail("STORAGE_UNAVAILABLE", "Object storage could not abort the upload.", 503)
    locked, current, _current_quota = _lock_session(session, owner, video_id)
    if locked.status in {UploadStatus.aborted, UploadStatus.expired, UploadStatus.completed}:
        session.rollback()
        return
    _release(_current_quota, locked)
    locked.status = UploadStatus.aborted
    if current.processing_status is ProcessingStatus.uploading:
        current.processing_status = ProcessingStatus.abandoned
    session.commit()


def latest_session(session: Session, owner: User, video_id: uuid.UUID) -> UploadSession:
    _owned_video(session, owner, video_id)
    upload = session.scalar(
        select(UploadSession)
        .where(UploadSession.video_id == video_id)
        .order_by(UploadSession.created_at.desc())
    )
    if upload is None:
        raise video_not_found()
    return upload


def processing_view(session: Session, owner: User, video_id: uuid.UUID) -> dict[str, object]:
    video, _channel = _owned_video(session, owner, video_id)
    job = session.scalar(
        select(VideoProcessingJob)
        .where(VideoProcessingJob.video_id == video.id)
        .order_by(VideoProcessingJob.created_at.desc())
    )
    error_code = None if job is None else job.error_code
    if error_code is None and video.processing_status is ProcessingStatus.failed:
        upload = session.scalar(
            select(UploadSession)
            .where(UploadSession.video_id == video.id)
            .order_by(UploadSession.created_at.desc())
        )
        if upload is not None:
            error_code = upload.error_code
    return {
        "processing_status": video.processing_status,
        "attempt": None if job is None else job.attempt,
        "error_code": error_code,
        "duration_seconds": video.duration_seconds,
        "width": video.width,
        "height": video.height,
        "auto_thumbnail_ready": video.auto_thumbnail_key is not None,
    }


def retry_processing(session: Session, owner: User, video_id: uuid.UUID) -> None:
    video, _channel = _owned_video(session, owner, video_id)
    if video.processing_status is not ProcessingStatus.failed or video.original_object_key is None:
        _fail("INVALID_STATE", "This video cannot be processed again.", 409)
    total = _job_count(session, video.id)
    if total >= _MAX_JOBS:
        _fail("INVALID_STATE", "This video has no processing attempts left.", 409)
    video.processing_status = ProcessingStatus.uploaded
    _insert_job(session, video.id, total + 1)
    session.commit()


def sweep_uploads(session: Session) -> None:
    now = datetime.now(UTC)
    _expire_sessions(session, now)
    _finish_stale_completions(session, now)
    _abandon_old_drafts(session, now)
    _repair_missing_jobs(session)
    _requeue_stale_jobs(session, now)
    _abort_orphan_uploads(session)
    session.commit()


def _require_parts(
    upload: UploadSession,
    stored: list[StoredPart],
    client_parts: list[tuple[int, str]] | None,
) -> None:
    expected_numbers = set(range(1, upload.part_count + 1))
    stored_numbers = [part.part_number for part in stored]
    if set(stored_numbers) != expected_numbers or len(stored_numbers) != len(expected_numbers):
        raise _Rejected()
    by_number = {part.part_number: part for part in stored}
    total = 0
    for number in expected_numbers:
        part = by_number[number]
        expected = part_length(upload.expected_size, upload.part_size, upload.part_count, number)
        if part.size != expected:
            raise _Rejected()
        total += part.size
    if total != upload.expected_size:
        raise _Rejected()
    if client_parts is None:
        return
    if len(client_parts) != len(expected_numbers):
        raise _Rejected()
    seen: set[int] = set()
    for number, etag in client_parts:
        if number in seen or number not in by_number:
            raise _Rejected()
        seen.add(number)
        if normalize_etag(etag) != normalize_etag(by_number[number].etag):
            raise _Rejected()


def _locked_upload(session: Session, upload_id: uuid.UUID) -> UploadSession | None:
    return session.scalar(
        select(UploadSession).where(UploadSession.id == upload_id).with_for_update()
    )


def _finish(session: Session, upload_id: uuid.UUID, token: uuid.UUID) -> None:
    upload = _locked_upload(session, upload_id)
    if upload is None or upload.completion_token != token:
        session.rollback()
        return
    if upload.status is UploadStatus.completed:
        session.rollback()
        return
    video = session.get(Video, upload.video_id)
    if video is None:
        session.rollback()
        return
    channel = session.get(Channel, video.channel_id)
    if channel is None:
        session.rollback()
        return
    quota = _lock_quota(session, channel.user_id)
    _release(quota, upload)
    quota.bytes_committed += upload.expected_size
    upload.status = UploadStatus.completed
    upload.completed_at = datetime.now(UTC)
    upload.error_code = None
    video.processing_status = ProcessingStatus.uploaded
    video.original_object_key = upload.object_key
    video.source_byte_size = upload.expected_size
    if _active_job_id(session, video.id) is None and _job_count(session, video.id) < _MAX_JOBS:
        _insert_job(session, video.id, _job_count(session, video.id) + 1)
    session.commit()


def _reject(session: Session, upload_id: uuid.UUID, token: uuid.UUID) -> None:
    upload = session.get(UploadSession, upload_id)
    if upload is None:
        return
    try:
        if upload.s3_upload_id is not None:
            abort_multipart(upload.object_key, upload.s3_upload_id)
        if head_size(upload.object_key) is not None:
            delete_object(upload.object_key)
    except StorageError:
        locked = _locked_upload(session, upload_id)
        if locked is not None and locked.completion_token == token:
            locked.error_code = "upload_rejected"
            session.commit()
        return
    locked = _locked_upload(session, upload_id)
    if locked is None or locked.completion_token != token:
        session.rollback()
        return
    video = session.get(Video, locked.video_id)
    channel = None if video is None else session.get(Channel, video.channel_id)
    if video is None or channel is None:
        session.rollback()
        return
    quota = _lock_quota(session, channel.user_id)
    _release(quota, locked)
    locked.status = UploadStatus.aborted
    locked.error_code = "upload_rejected"
    video.processing_status = ProcessingStatus.failed
    video.original_object_key = None
    session.commit()


def _return_to_initiated(session: Session, upload_id: uuid.UUID, token: uuid.UUID) -> None:
    upload = _locked_upload(session, upload_id)
    still_completing = upload is not None and upload.status is UploadStatus.completing
    if upload is None or upload.completion_token != token or not still_completing:
        session.rollback()
        return
    upload.status = UploadStatus.initiated
    upload.completion_token = None
    upload.completion_started_at = None
    session.commit()


def _release_failed_create(session: Session, upload_id: uuid.UUID) -> None:
    upload = _locked_upload(session, upload_id)
    if upload is None:
        return
    video = session.get(Video, upload.video_id)
    channel = None if video is None else session.get(Channel, video.channel_id)
    if video is None or channel is None:
        session.rollback()
        return
    quota = _lock_quota(session, channel.user_id)
    _release(quota, upload)
    upload.status = UploadStatus.aborted
    if video.processing_status is ProcessingStatus.draft:
        video.processing_status = ProcessingStatus.draft
    session.commit()


def _expire_sessions(session: Session, now: datetime) -> None:
    stale_creates = session.scalars(
        select(UploadSession).where(
            UploadSession.status == UploadStatus.initiated,
            UploadSession.s3_upload_id.is_(None),
            UploadSession.created_at <= now - timedelta(minutes=5),
        )
    ).all()
    for upload in stale_creates:
        _abort_and_release(session, upload, UploadStatus.aborted, ProcessingStatus.abandoned)
    rows = session.scalars(
        select(UploadSession).where(
            UploadSession.status == UploadStatus.initiated,
            UploadSession.expires_at <= now,
        )
    ).all()
    for upload in rows:
        _abort_and_release(session, upload, UploadStatus.expired, ProcessingStatus.abandoned)


def _finish_stale_completions(session: Session, now: datetime) -> None:
    rows = session.scalars(
        select(UploadSession).where(UploadSession.status == UploadStatus.completing)
    ).all()
    for upload in rows:
        if upload.error_code:
            _abort_and_release(session, upload, UploadStatus.aborted, ProcessingStatus.failed)
            continue
        started = upload.completion_started_at
        if started is None or now - started < _LOCK_WINDOW:
            continue
        if upload.s3_upload_id is None:
            continue
        try:
            size = head_size(upload.object_key)
        except StorageError:
            continue
        if size == upload.expected_size and upload.completion_token is not None:
            _finish(session, upload.id, upload.completion_token)
            continue
        if size is not None or upload.s3_upload_id is None or upload.completion_token is None:
            continue
        try:
            stored = list_parts(upload.object_key, upload.s3_upload_id)
            _require_parts(upload, stored, None)
            complete_multipart(upload.object_key, upload.s3_upload_id, stored)
            confirmed = head_size(upload.object_key)
        except (StorageError, _Rejected):
            _abort_and_release(session, upload, UploadStatus.aborted, ProcessingStatus.failed)
            continue
        if confirmed == upload.expected_size:
            _finish(session, upload.id, upload.completion_token)


def _abandon_old_drafts(session: Session, now: datetime) -> None:
    cutoff = now - timedelta(hours=_SESSION_HOURS)
    rows = session.scalars(
        select(Video).where(
            Video.processing_status == ProcessingStatus.draft,
            Video.created_at <= cutoff,
            Video.deleted_at.is_(None),
        )
    ).all()
    for video in rows:
        video.processing_status = ProcessingStatus.abandoned


def _repair_missing_jobs(session: Session) -> None:
    rows = session.scalars(
        select(Video).where(
            Video.processing_status == ProcessingStatus.uploaded,
            Video.original_object_key.is_not(None),
            Video.deleted_at.is_(None),
        )
    ).all()
    for video in rows:
        needs_media = (
            video.duration_seconds is None
            or video.auto_thumbnail_key is None
            or video.hls_master_key is None
        )
        if (
            needs_media
            and _active_job_id(session, video.id) is None
            and _job_count(session, video.id) < _MAX_JOBS
        ):
            _insert_job(session, video.id, _job_count(session, video.id) + 1)


def _requeue_stale_jobs(session: Session, now: datetime) -> None:
    rows = session.scalars(
        select(VideoProcessingJob).where(
            VideoProcessingJob.status == JobStatus.running,
            VideoProcessingJob.locked_until.is_not(None),
            VideoProcessingJob.locked_until <= now,
        )
    ).all()
    for job in rows:
        job.status = JobStatus.queued
        job.locked_by = None
        job.locked_until = None
        job.next_run_at = now
        video = session.get(Video, job.video_id)
        if video is not None and video.processing_status is ProcessingStatus.processing:
            video.processing_status = ProcessingStatus.uploaded


def _abort_orphan_uploads(session: Session) -> None:
    stored_ids = select(UploadSession.s3_upload_id).where(UploadSession.s3_upload_id.is_not(None))
    known = set(session.scalars(stored_ids).all())
    try:
        orphans = list_orphan_uploads({item for item in known if item is not None})
    except StorageError:
        return
    cutoff = datetime.now(UTC) - timedelta(minutes=15)
    for key, upload_id, started in orphans:
        if started is not None and started.astimezone(UTC) > cutoff:
            continue
        try:
            abort_multipart(key, upload_id)
        except StorageError:
            continue


def _abort_and_release(
    session: Session,
    upload: UploadSession,
    status: UploadStatus,
    video_status: ProcessingStatus,
) -> None:
    try:
        if upload.s3_upload_id is not None:
            abort_multipart(upload.object_key, upload.s3_upload_id)
        if head_size(upload.object_key) is not None:
            delete_object(upload.object_key)
    except StorageError:
        return
    video = session.get(Video, upload.video_id)
    channel = None if video is None else session.get(Channel, video.channel_id)
    if video is None or channel is None:
        return
    quota = _lock_quota(session, channel.user_id)
    _release(quota, upload)
    upload.status = status
    if video.processing_status in {ProcessingStatus.uploading, ProcessingStatus.draft}:
        video.processing_status = video_status


def _insert_job(session: Session, video_id: uuid.UUID, attempt: int) -> None:
    session.add(
        VideoProcessingJob(
            id=uuid7(),
            video_id=video_id,
            attempt=attempt,
            status=JobStatus.queued,
            next_run_at=datetime.now(UTC),
        )
    )


def _owned_video(session: Session, owner: User, video_id: uuid.UUID) -> tuple[Video, Channel]:
    video = session.get(Video, video_id)
    if video is None or video.deleted_at is not None:
        raise video_not_found()
    channel = session.get(Channel, video.channel_id)
    if channel is None or channel.user_id != owner.id:
        raise video_not_found()
    return video, channel


def _active_session(session: Session, owner: User, video_id: uuid.UUID) -> UploadSession:
    _owned_video(session, owner, video_id)
    upload = session.scalar(
        select(UploadSession)
        .where(
            UploadSession.video_id == video_id,
            UploadSession.status.in_((UploadStatus.initiated, UploadStatus.completing)),
        )
        .order_by(UploadSession.created_at.desc())
    )
    if upload is None:
        raise video_not_found()
    return upload


def _lock_session(
    session: Session, owner: User, video_id: uuid.UUID
) -> tuple[UploadSession, Video, UserStorageQuota]:
    video, channel = _owned_video(session, owner, video_id)
    upload = session.scalar(
        select(UploadSession)
        .where(UploadSession.video_id == video.id)
        .order_by(UploadSession.created_at.desc())
        .with_for_update()
    )
    if upload is None:
        raise video_not_found()
    quota = _lock_quota(session, channel.user_id)
    return upload, video, quota


def _lock_quota(session: Session, user_id: uuid.UUID) -> UserStorageQuota:
    quota = session.scalar(
        select(UserStorageQuota).where(UserStorageQuota.user_id == user_id).with_for_update()
    )
    if quota is None:
        _fail("INVALID_STATE", "Storage quota is not available.", 409)
    return quota


def _active_upload_count(session: Session, user_id: uuid.UUID) -> int:
    count = session.scalar(
        select(func.count())
        .select_from(UploadSession)
        .join(Video, Video.id == UploadSession.video_id)
        .join(Channel, Channel.id == Video.channel_id)
        .where(
            Channel.user_id == user_id,
            UploadSession.status.in_((UploadStatus.initiated, UploadStatus.completing)),
        )
    )
    return int(count or 0)


def _job_count(session: Session, video_id: uuid.UUID) -> int:
    count = session.scalar(
        select(func.count())
        .select_from(VideoProcessingJob)
        .where(VideoProcessingJob.video_id == video_id)
    )
    return int(count or 0)


def _active_job_id(session: Session, video_id: uuid.UUID) -> uuid.UUID | None:
    return session.scalar(
        select(VideoProcessingJob.id).where(
            VideoProcessingJob.video_id == video_id,
            VideoProcessingJob.status.in_((JobStatus.queued, JobStatus.running)),
        )
    )


def _release(quota: UserStorageQuota, upload: UploadSession) -> None:
    quota.bytes_reserved = max(0, quota.bytes_reserved - upload.expected_size)


def _reject_expired(upload: UploadSession) -> None:
    if upload.expires_at <= datetime.now(UTC) or upload.status is UploadStatus.expired:
        _fail("UPLOAD_EXPIRED", "This upload has expired.", 409)


def _lock_is_fresh(upload: UploadSession) -> bool:
    started = upload.completion_started_at
    return started is not None and datetime.now(UTC) - started < _LOCK_WINDOW


def _reload_video(session: Session, video_id: uuid.UUID) -> Video:
    video = session.get(Video, video_id)
    if video is None:
        raise video_not_found()
    return video


def _fail(code: str, message: str, status: int) -> None:
    raise AppError(code, message, status)


class _Rejected(Exception):
    """Part verification failed. The caller turns this into UPLOAD_REJECTED."""
