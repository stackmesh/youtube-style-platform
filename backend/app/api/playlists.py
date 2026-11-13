"""Playlist routes. Item visibility follows the video ACL."""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_optional_user
from app.db.models import PlaylistVisibility, User
from app.db.session import get_db
from app.identity.schemas import ErrorResponse
from app.playlists.service import (
    add_item,
    create_playlist,
    delete_playlist,
    list_channel_playlists,
    read_playlist,
    remove_item,
    reorder_items,
    update_playlist,
)

router = APIRouter(tags=["playlists"])

_ERRORS = {
    401: {"model": ErrorResponse, "description": "Authentication is required."},
    404: {"model": ErrorResponse, "description": "Playlist not found."},
    422: {"model": ErrorResponse, "description": "The request failed validation."},
}


class PlaylistWrite(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    visibility: PlaylistVisibility = PlaylistVisibility.private


class PlaylistPatch(BaseModel):
    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    visibility: PlaylistVisibility | None = None


class ItemCreate(BaseModel):
    video_id: uuid.UUID


class ItemOrder(BaseModel):
    video_ids: list[uuid.UUID] = Field(max_length=500)


@router.post("/api/v1/playlists", responses=_ERRORS)
def create(
    body: PlaylistWrite,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    playlist = create_playlist(session, user, body.title, body.description, body.visibility)
    return read_playlist(session, playlist.id, user)


@router.get("/api/v1/playlists/{playlist_id}", responses={404: _ERRORS[404]})
def read(
    playlist_id: uuid.UUID,
    viewer: User | None = Depends(get_optional_user),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    return read_playlist(session, playlist_id, viewer)


@router.patch("/api/v1/playlists/{playlist_id}", responses=_ERRORS)
def patch(
    playlist_id: uuid.UUID,
    body: PlaylistPatch,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    update_playlist(session, user, playlist_id, body.title, body.description, body.visibility)
    return read_playlist(session, playlist_id, user)


@router.delete("/api/v1/playlists/{playlist_id}", status_code=204, responses=_ERRORS)
def remove(
    playlist_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    delete_playlist(session, user, playlist_id)


@router.post("/api/v1/playlists/{playlist_id}/items", status_code=204, responses=_ERRORS)
def create_item(
    playlist_id: uuid.UUID,
    body: ItemCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    add_item(session, user, playlist_id, body.video_id)


@router.delete(
    "/api/v1/playlists/{playlist_id}/items/{video_id}",
    status_code=204,
    responses=_ERRORS,
)
def delete_item(
    playlist_id: uuid.UUID,
    video_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    remove_item(session, user, playlist_id, video_id)


@router.patch("/api/v1/playlists/{playlist_id}/items", status_code=204, responses=_ERRORS)
def order_items(
    playlist_id: uuid.UUID,
    body: ItemOrder,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    reorder_items(session, user, playlist_id, body.video_ids)


@router.get("/api/v1/channels/{handle}/playlists")
def channel_playlists(
    handle: str,
    viewer: User | None = Depends(get_optional_user),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    rows = list_channel_playlists(session, handle, viewer)
    return {
        "items": [
            {
                "id": row.id,
                "title": row.title,
                "description": row.description,
                "visibility": row.visibility,
            }
            for row in rows
        ]
    }
