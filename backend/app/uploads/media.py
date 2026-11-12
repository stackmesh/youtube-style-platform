"""Probe an uploaded original and store a private thumbnail. HLS stays in Stage 05."""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.ids import uuid7
from app.db.models import (
    Channel,
    FailureClass,
    JobStatus,
    ProcessingStatus,
    UserStorageQuota,
    Video,
    VideoProcessingJob,
)
from app.uploads.objects import StorageError, get_to_path, head_size, put_bytes, thumbnail_key

_AUTOMATIC_JOBS = 3
_MAX_JOBS = 6
_DETAIL_LIMIT = 500
_MIN_DIMENSION = 64
_MAX_DIMENSION = 3840
_BACKOFF_MINUTES = (1, 5, 15)
_DISK_BACKOFF = timedelta(minutes=30)
_LOCK = timedelta(hours=3)
_GIBIBYTE = 1024 * 1024 * 1024


class MediaError(Exception):
    def __init__(self, code: str, *, fatal: bool, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.fatal = fatal
        self.detail = detail[:_DETAIL_LIMIT]


@dataclass(frozen=True)
class ProbeResult:
    duration_seconds: int
    width: int
    height: int
    video_codec: str
    audio_codec: str | None
    container: str


def claim_next_job(session: Session, worker_id: str) -> uuid.UUID | None:
    now = datetime.now(UTC)
    job = session.scalar(
        select(VideoProcessingJob)
        .where(
            VideoProcessingJob.status == JobStatus.queued,
            VideoProcessingJob.next_run_at <= now,
        )
        .order_by(VideoProcessingJob.next_run_at, VideoProcessingJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        session.rollback()
        return None
    video = session.get(Video, job.video_id)
    if video is None or video.deleted_at is not None:
        job.status = JobStatus.cancelled
        job.finished_at = now
        session.commit()
        return None
    job.status = JobStatus.running
    job.locked_by = worker_id
    job.locked_until = now + _LOCK
    job.started_at = now
    video.processing_status = ProcessingStatus.processing
    session.commit()
    return job.id


def execute_claimed_job(session: Session, job_id: uuid.UUID) -> None:
    job = session.get(VideoProcessingJob, job_id)
    if job is None or job.status is not JobStatus.running:
        return
    video = session.get(Video, job.video_id)
    if video is None or video.deleted_at is not None or video.original_object_key is None:
        _cancel(session, job, video)
        return
    work = Path(get_settings().work_dir) / str(job.id)
    source = work / "source"
    frame = work / "frame.jpg"
    try:
        _prepare_work(work)
        _require_disk(video.source_byte_size or 0)
        get_to_path(video.original_object_key, str(source))
        digest = _sha256(source)
        probe = probe_file(source)
        encoded = encode_thumbnail(source, frame, probe.duration_seconds)
        key = thumbnail_key(str(video.id))
        put_bytes(key, encoded, "image/jpeg")
        stored = head_size(key)
        if stored is None:
            raise MediaError("storage_error", fatal=False)
        _succeed(session, job, video, probe, digest, key, stored)
    except MediaError as exc:
        _fail_job(session, job_id, exc)
    except StorageError:
        _fail_job(session, job_id, MediaError("storage_error", fatal=False))
    except OSError:
        _fail_job(session, job_id, MediaError("ffmpeg_failed", fatal=False))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def probe_file(path: Path) -> ProbeResult:
    settings = get_settings()
    try:
        completed = _run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                "-protocol_whitelist",
                "file,pipe",
                str(path),
            ],
            30,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError("ffmpeg_timeout", fatal=False, detail=_stderr(exc.stderr)) from exc
    if completed.returncode != 0:
        raise MediaError("invalid_media", fatal=True, detail=_stderr(completed.stderr))
    try:
        payload = json.loads(completed.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise MediaError("invalid_media", fatal=True) from exc
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise MediaError("invalid_media", fatal=True)
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    if not isinstance(video_stream, dict):
        raise MediaError("invalid_media", fatal=True)
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
    try:
        width = int(video_stream["width"])
        height = int(video_stream["height"])
        duration = float(payload["format"]["duration"])
        container = str(payload["format"]["format_name"])
        video_codec = str(video_stream["codec_name"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MediaError("invalid_media", fatal=True) from exc
    if duration < 1 or duration > settings.max_duration_seconds:
        raise MediaError("duration_exceeded", fatal=True)
    too_small = width < _MIN_DIMENSION or height < _MIN_DIMENSION
    too_large = width > _MAX_DIMENSION or height > _MAX_DIMENSION
    if too_small or too_large:
        raise MediaError("invalid_media", fatal=True)
    audio_codec = None
    if isinstance(audio_stream, dict) and audio_stream.get("codec_name"):
        audio_codec = str(audio_stream["codec_name"])
    return ProbeResult(
        duration_seconds=max(1, int(duration)),
        width=width,
        height=height,
        video_codec=video_codec,
        audio_codec=audio_codec,
        container=container,
    )


def encode_thumbnail(source: Path, destination: Path, duration_seconds: int) -> bytes:
    offset = 1 if duration_seconds <= 10 else max(1, int(duration_seconds * 0.1))
    try:
        completed = _run(
            [
                "ffmpeg",
                "-nostdin",
                "-protocol_whitelist",
                "file,pipe",
                "-y",
                "-ss",
                str(offset),
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                "scale='min(1280,iw)':-2",
                str(destination),
            ],
            60,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError("ffmpeg_timeout", fatal=False, detail=_stderr(exc.stderr)) from exc
    if completed.returncode != 0 or not destination.is_file():
        raise MediaError("ffmpeg_failed", fatal=False, detail=_stderr(completed.stderr))
    return _reencode_jpeg(destination)


def _reencode_jpeg(path: Path) -> bytes:
    try:
        from PIL import Image
    except ImportError as exc:
        raise MediaError("ffmpeg_failed", fatal=False) from exc
    try:
        with Image.open(path) as image:
            frame = image.convert("RGB")
            frame.save(path, format="JPEG", quality=85, optimize=True)
    except OSError as exc:
        raise MediaError("ffmpeg_failed", fatal=False) from exc
    return path.read_bytes()


def _succeed(
    session: Session,
    job: VideoProcessingJob,
    video: Video,
    probe: ProbeResult,
    digest: str,
    key: str,
    thumbnail_size: int,
) -> None:
    current_video = session.get(Video, video.id)
    current_job = session.get(VideoProcessingJob, job.id)
    if current_video is None or current_job is None:
        return
    if current_video.deleted_at is not None or current_job.status is JobStatus.cancelled:
        current_job.status = JobStatus.cancelled
        current_job.finished_at = datetime.now(UTC)
        session.commit()
        return
    channel = session.get(Channel, current_video.channel_id)
    if channel is None:
        return
    quota = session.scalar(
        select(UserStorageQuota)
        .where(UserStorageQuota.user_id == channel.user_id)
        .with_for_update()
    )
    if quota is not None:
        quota.bytes_committed += thumbnail_size
    now = datetime.now(UTC)
    current_job.status = JobStatus.succeeded
    current_job.finished_at = now
    current_job.locked_by = None
    current_job.locked_until = None
    current_job.error_code = None
    current_job.failure_class = None
    current_video.processing_status = ProcessingStatus.uploaded
    current_video.duration_seconds = probe.duration_seconds
    current_video.width = probe.width
    current_video.height = probe.height
    current_video.auto_thumbnail_key = key
    current_video.source_sha256 = digest
    session.commit()


def _fail_job(session: Session, job_id: uuid.UUID, error: MediaError) -> None:
    session.rollback()
    job = session.get(VideoProcessingJob, job_id)
    if job is None:
        return
    video = session.get(Video, job.video_id)
    now = datetime.now(UTC)
    job.status = JobStatus.failed
    job.failure_class = FailureClass.fatal if error.fatal else FailureClass.retryable
    job.error_code = error.code
    job.error_detail = error.detail or None
    job.finished_at = now
    job.locked_by = None
    job.locked_until = None
    if video is None:
        session.commit()
        return
    if video.deleted_at is not None:
        job.status = JobStatus.cancelled
        session.commit()
        return
    count = len(
        session.scalars(
            select(VideoProcessingJob.id).where(VideoProcessingJob.video_id == video.id)
        ).all()
    )
    if not error.fatal and count < _AUTOMATIC_JOBS and count < _MAX_JOBS:
        video.processing_status = ProcessingStatus.uploaded
        delay = (
            _DISK_BACKOFF
            if error.code == "insufficient_disk"
            else timedelta(minutes=_BACKOFF_MINUTES[min(count, 3) - 1])
        )
        session.add(
            VideoProcessingJob(
                id=uuid7(),
                video_id=video.id,
                attempt=count + 1,
                status=JobStatus.queued,
                next_run_at=now + delay,
            )
        )
    else:
        video.processing_status = ProcessingStatus.failed
    session.commit()


def _cancel(session: Session, job: VideoProcessingJob, video: Video | None) -> None:
    job.status = JobStatus.cancelled
    job.finished_at = datetime.now(UTC)
    job.locked_by = None
    job.locked_until = None
    if video is not None and video.processing_status is ProcessingStatus.processing:
        video.processing_status = ProcessingStatus.uploaded
    session.commit()


def _prepare_work(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=False)


def _require_disk(source_size: int) -> None:
    root = Path(get_settings().work_dir)
    root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(root).free
    if free < (3 * source_size) + _GIBIBYTE:
        raise MediaError("insufficient_disk", fatal=False)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        preexec_fn=_limit_process,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def _limit_process() -> None:
    import resource

    cpu = 120
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))


def _stderr(payload: bytes | None) -> str:
    if not payload:
        return ""
    return payload.decode("utf-8", errors="replace")[:_DETAIL_LIMIT]
