"""Custom thumbnail and avatar uploads. The stored object is a re-encoded JPEG."""

import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.channels import _summary
from app.api.deps import get_current_user
from app.core.exceptions import AppError
from app.db.models import User
from app.db.session import get_db
from app.identity.schemas import ChannelSummary, ErrorResponse
from app.uploads.images import (
    delete_avatar,
    delete_custom_thumbnail,
    store_avatar,
    store_custom_thumbnail,
)

router = APIRouter(tags=["images"])

_ERRORS = {
    401: {"model": ErrorResponse, "description": "Authentication is required."},
    404: {"model": ErrorResponse, "description": "Not found."},
    422: {"model": ErrorResponse, "description": "The image failed validation."},
    503: {"model": ErrorResponse, "description": "Object storage is unavailable."},
}


@router.put(
    "/api/v1/videos/{video_id}/thumbnail",
    status_code=204,
    responses=_ERRORS,
)
async def put_thumbnail(
    video_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> Response:
    """Store a custom thumbnail. The auto thumbnail, when it exists, is left in place."""
    store_custom_thumbnail(session, user, video_id, await _image_body(request))
    return Response(status_code=204)


@router.delete(
    "/api/v1/videos/{video_id}/thumbnail",
    status_code=204,
    responses=_ERRORS,
)
def remove_thumbnail(
    video_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> Response:
    """Remove the custom thumbnail. The auto thumbnail remains."""
    delete_custom_thumbnail(session, user, video_id)
    return Response(status_code=204)


@router.put("/api/v1/channels/me/avatar", response_model=ChannelSummary, responses=_ERRORS)
async def put_avatar(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> ChannelSummary:
    """Store the caller's avatar. The response does not include an object URL."""
    return _summary(store_avatar(session, user, await _image_body(request)))


@router.delete("/api/v1/channels/me/avatar", status_code=204, responses=_ERRORS)
def remove_avatar(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> Response:
    """Remove the caller's avatar."""
    delete_avatar(session, user)
    return Response(status_code=204)


async def _image_body(request: Request) -> bytes:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise AppError("VALIDATION_ERROR", "Choose an image file.", 422)
        body = await upload.read()
        await request.close()
        return bytes(body)
    return await request.body()
