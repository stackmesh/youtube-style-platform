"""Owner upload and processing routes. Bytes go to object storage, not to this API."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.network import client_ip
from app.api.videos import _detail
from app.catalogue.schemas import VideoDetail
from app.catalogue.service import read_video
from app.core.rate_limit import enforce_rate_limit
from app.db.models import UploadSession, User
from app.db.session import get_db
from app.identity.schemas import ErrorResponse
from app.uploads.service import (
    abort_upload,
    complete_upload,
    initiate_upload,
    latest_session,
    presign_parts,
    processing_view,
    retry_processing,
)

router = APIRouter(tags=["uploads"])

_ERRORS = {
    401: {"model": ErrorResponse, "description": "Authentication is required."},
    404: {"model": ErrorResponse, "description": "Video not found."},
    409: {"model": ErrorResponse, "description": "The upload cannot accept this call."},
    422: {"model": ErrorResponse, "description": "The request failed validation."},
    429: {"model": ErrorResponse, "description": "Rate limit exceeded. Retry-After is set."},
    503: {"model": ErrorResponse, "description": "Object storage is unavailable."},
}


class UploadCreateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    byte_size: int = Field(gt=0)
    content_type: str = Field(min_length=1, max_length=100)


class UploadSessionResponse(BaseModel):
    session_id: uuid.UUID
    status: str
    part_size: int
    part_count: int
    expected_size: int
    expires_at: datetime
    completed_at: datetime | None = None
    error_code: str | None = None


class PartRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    part_numbers: list[int]


class PartInstruction(BaseModel):
    part_number: int
    url: str
    content_length: int
    expires_at: datetime


class PartListResponse(BaseModel):
    parts: list[PartInstruction]


class CompletedPart(BaseModel):
    model_config = ConfigDict(extra="ignore")

    part_number: int
    etag: str = Field(min_length=1, max_length=200)


class CompleteRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    parts: list[CompletedPart]


class ProcessingResponse(BaseModel):
    processing_status: str
    attempt: int | None
    error_code: str | None
    duration_seconds: int | None
    width: int | None
    height: int | None
    auto_thumbnail_ready: bool


@router.post(
    "/api/v1/videos/{video_id}/upload",
    response_model=UploadSessionResponse,
    responses=_ERRORS,
)
def create_upload(
    video_id: uuid.UUID,
    body: UploadCreateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> UploadSessionResponse:
    """Reserve quota and start a multipart upload. The object key is chosen here."""
    enforce_rate_limit(f"rl:upload:ip:{client_ip(request)}", 30, 3600)
    enforce_rate_limit(f"rl:upload:user:{user.id}", 10, 3600)
    upload = initiate_upload(session, user, video_id, body.byte_size, body.content_type)
    return _session(upload)


@router.get(
    "/api/v1/videos/{video_id}/upload",
    response_model=UploadSessionResponse,
    responses=_ERRORS,
)
def read_upload(
    video_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> UploadSessionResponse:
    """Return the latest upload session for a video the caller owns."""
    return _session(latest_session(session, user, video_id))


@router.post(
    "/api/v1/videos/{video_id}/upload/parts",
    response_model=PartListResponse,
    responses=_ERRORS,
)
def sign_parts(
    video_id: uuid.UUID,
    body: PartRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> PartListResponse:
    """Return short-lived part URLs. Each URL is signed for one content length."""
    parts = presign_parts(session, user, video_id, body.part_numbers)
    return PartListResponse(parts=[PartInstruction.model_validate(part) for part in parts])


@router.post(
    "/api/v1/videos/{video_id}/upload/complete",
    response_model=VideoDetail,
    response_model_exclude_none=True,
    responses=_ERRORS,
)
def finish_upload(
    video_id: uuid.UUID,
    body: CompleteRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> VideoDetail:
    """Verify stored parts and enqueue processing. A repeated call returns the same video."""
    complete_upload(
        session,
        user,
        video_id,
        [(part.part_number, part.etag) for part in body.parts],
    )
    return _detail(read_video(session, video_id, user))


@router.post(
    "/api/v1/videos/{video_id}/upload/abort",
    status_code=204,
    responses=_ERRORS,
)
def cancel_upload(
    video_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> Response:
    """Abort the multipart upload and release the quota reservation."""
    abort_upload(session, user, video_id)
    return Response(status_code=204)


@router.get(
    "/api/v1/videos/{video_id}/processing",
    response_model=ProcessingResponse,
    responses=_ERRORS,
)
def read_processing(
    video_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> ProcessingResponse:
    """Processing status for the owner. FFmpeg output is not included."""
    payload = processing_view(session, user, video_id)
    return ProcessingResponse(
        processing_status=str(payload["processing_status"].value),  # type: ignore[union-attr]
        attempt=payload["attempt"],  # type: ignore[arg-type]
        error_code=payload["error_code"],  # type: ignore[arg-type]
        duration_seconds=payload["duration_seconds"],  # type: ignore[arg-type]
        width=payload["width"],  # type: ignore[arg-type]
        height=payload["height"],  # type: ignore[arg-type]
        auto_thumbnail_ready=bool(payload["auto_thumbnail_ready"]),
    )


@router.post(
    "/api/v1/videos/{video_id}/processing/retry",
    status_code=204,
    responses=_ERRORS,
)
def retry(
    video_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> Response:
    """Queue another processing attempt when the original object still exists."""
    retry_processing(session, user, video_id)
    return Response(status_code=204)


def _session(upload: UploadSession) -> UploadSessionResponse:
    return UploadSessionResponse(
        session_id=upload.id,
        status=upload.status.value,
        part_size=upload.part_size,
        part_count=upload.part_count,
        expected_size=upload.expected_size,
        expires_at=upload.expires_at,
        completed_at=upload.completed_at,
        error_code=upload.error_code,
    )
