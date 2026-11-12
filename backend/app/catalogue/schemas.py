"""HTTP shapes for the catalogue and search."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.db.models import ModerationStatus, ProcessingStatus, Visibility


class VideoCreateRequest(BaseModel):
    title: str = Field(max_length=100)
    description: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=15)
    visibility: Visibility = Visibility.private


class VideoUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    tags: list[str] | None = Field(default=None, max_length=15)
    visibility: Visibility | None = None


class GrantCreateRequest(BaseModel):
    handle: str = Field(min_length=3, max_length=30)


class ChannelCard(BaseModel):
    handle: str
    name: str


class VideoSummary(BaseModel):
    id: UUID
    title: str
    thumbnail_url: str | None = None
    channel: ChannelCard
    view_count: int
    published_at: datetime | None = None
    duration_seconds: int | None = None
    visibility: Visibility | None = None
    processing_status: ProcessingStatus | None = None
    moderation_status: ModerationStatus | None = None


class VideoDetail(VideoSummary):
    description: str
    tags: list[str]
    like_count: int
    dislike_count: int
    comment_count: int
    viewer_subscribed: bool = False


class VideoPageResponse(BaseModel):
    items: list[VideoSummary]
    next_cursor: str | None


class VideoDetailPageResponse(BaseModel):
    items: list[VideoDetail]
    next_cursor: str | None = None


class GrantResponse(BaseModel):
    handle: str
    created_at: datetime
    revoked_at: datetime | None = None


class GrantListResponse(BaseModel):
    items: list[GrantResponse]


class ChannelHit(BaseModel):
    handle: str
    name: str


class ChannelHitPage(BaseModel):
    items: list[ChannelHit]
    next_cursor: str | None


class SearchResponse(BaseModel):
    videos: VideoPageResponse | None = None
    channels: ChannelHitPage | None = None
