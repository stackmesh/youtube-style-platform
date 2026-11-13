import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, ENUM, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserRole(enum.StrEnum):
    USER = "USER"
    MODERATOR = "MODERATOR"
    ADMIN = "ADMIN"


user_role_enum = ENUM(UserRole, name="user_role", create_type=False)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("session_epoch >= 1", name="users_session_epoch_check"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(
        user_role_enum, nullable=False, default=UserRole.USER, server_default="USER"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    session_epoch: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    channel: Mapped["Channel"] = relationship(back_populates="user")
    quota: Mapped["UserStorageQuota"] = relationship(back_populates="user")
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(back_populates="user")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("refresh_tokens.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class Channel(Base):
    __tablename__ = "channels"
    __table_args__ = (
        CheckConstraint("handle::text ~ '^[a-z0-9_]{3,30}$'", name="channels_handle_format"),
        CheckConstraint("subscriber_count >= 0", name="channels_subscriber_count_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    handle: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(
        String(2000), nullable=False, default="", server_default=""
    )
    avatar_object_key: Mapped[str | None] = mapped_column(Text)
    subscriber_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', name || ' ' || handle::text)", persisted=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="channel")


class UserStorageQuota(Base):
    __tablename__ = "user_storage_quotas"
    __table_args__ = (
        CheckConstraint("bytes_committed >= 0", name="user_storage_quotas_committed_check"),
        CheckConstraint("bytes_reserved >= 0", name="user_storage_quotas_reserved_check"),
        CheckConstraint("bytes_limit > 0", name="user_storage_quotas_limit_check"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    bytes_committed: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    bytes_reserved: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    bytes_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)

    user: Mapped[User] = relationship(back_populates="quota")


class Visibility(enum.StrEnum):
    public = "public"
    unlisted = "unlisted"
    private = "private"


class ProcessingStatus(enum.StrEnum):
    draft = "draft"
    uploading = "uploading"
    uploaded = "uploaded"
    processing = "processing"
    ready = "ready"
    failed = "failed"
    abandoned = "abandoned"


class ModerationStatus(enum.StrEnum):
    visible = "visible"
    hidden = "hidden"


def _enum_values(enum_type: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_type]


visibility_enum = ENUM(
    Visibility,
    name="visibility",
    create_type=False,
    values_callable=_enum_values,
)
processing_status_enum = ENUM(
    ProcessingStatus,
    name="processing_status",
    create_type=False,
    values_callable=_enum_values,
)
moderation_status_enum = ENUM(
    ModerationStatus,
    name="moderation_status",
    create_type=False,
    values_callable=_enum_values,
)


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (
        CheckConstraint("char_length(title) >= 1", name="videos_title_check"),
        CheckConstraint("acl_version >= 1", name="videos_acl_version_check"),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds > 0",
            name="videos_duration_check",
        ),
        CheckConstraint("width IS NULL OR width > 0", name="videos_width_check"),
        CheckConstraint("height IS NULL OR height > 0", name="videos_height_check"),
        CheckConstraint("view_count >= 0", name="videos_view_count_check"),
        CheckConstraint("like_count >= 0", name="videos_like_count_check"),
        CheckConstraint("dislike_count >= 0", name="videos_dislike_count_check"),
        CheckConstraint("comment_count >= 0", name="videos_comment_count_check"),
        CheckConstraint(
            "source_byte_size IS NULL OR source_byte_size > 0",
            name="videos_source_size_check",
        ),
        CheckConstraint(
            "processing_status <> 'ready' OR published_at IS NOT NULL",
            name="videos_ready_published_check",
        ),
        CheckConstraint(
            "source_sha256 IS NULL OR source_sha256 ~ '^[a-f0-9]{64}$'",
            name="videos_source_sha256_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(
        String(5000), nullable=False, default="", server_default=""
    )
    visibility: Mapped[Visibility] = mapped_column(
        visibility_enum, nullable=False, default=Visibility.private, server_default="private"
    )
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        processing_status_enum,
        nullable=False,
        default=ProcessingStatus.draft,
        server_default="draft",
    )
    moderation_status: Mapped[ModerationStatus] = mapped_column(
        moderation_status_enum,
        nullable=False,
        default=ModerationStatus.visible,
        server_default="visible",
    )
    acl_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    view_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    like_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    dislike_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    comment_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    original_object_key: Mapped[str | None] = mapped_column(Text)
    hls_master_key: Mapped[str | None] = mapped_column(Text)
    auto_thumbnail_key: Mapped[str | None] = mapped_column(Text)
    custom_thumbnail_key: Mapped[str | None] = mapped_column(Text)
    source_byte_size: Mapped[int | None] = mapped_column(BigInteger)
    source_content_type: Mapped[str | None] = mapped_column(Text)
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', title || ' ' || description)", persisted=True),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    storage_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    channel: Mapped[Channel] = relationship()
    tags: Mapped[list["VideoTag"]] = relationship(back_populates="video")


class VideoTag(Base):
    __tablename__ = "video_tags"
    __table_args__ = (
        CheckConstraint("tag ~ '^[a-z0-9][a-z0-9-]{0,29}$'", name="video_tags_format"),
    )

    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="RESTRICT"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(String(30), primary_key=True)

    video: Mapped[Video] = relationship(back_populates="tags")


class VideoAccessGrant(Base):
    __tablename__ = "video_access_grants"
    __table_args__ = (
        CheckConstraint(
            "grantee_user_id <> granted_by_user_id",
            name="video_access_grants_not_self",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="RESTRICT"), nullable=False
    )
    grantee_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    granted_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    video: Mapped[Video] = relationship()


class UploadStatus(enum.StrEnum):
    initiated = "initiated"
    completing = "completing"
    completed = "completed"
    aborted = "aborted"
    expired = "expired"


class JobStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class FailureClass(enum.StrEnum):
    fatal = "fatal"
    retryable = "retryable"


upload_status_enum = ENUM(
    UploadStatus,
    name="upload_status",
    create_type=False,
    values_callable=_enum_values,
)
job_status_enum = ENUM(
    JobStatus,
    name="job_status",
    create_type=False,
    values_callable=_enum_values,
)
failure_class_enum = ENUM(
    FailureClass,
    name="failure_class",
    create_type=False,
    values_callable=_enum_values,
)


class UploadSession(Base):
    __tablename__ = "upload_sessions"
    __table_args__ = (
        CheckConstraint("expected_size > 0", name="upload_sessions_size_check"),
        CheckConstraint("part_size = 16777216", name="upload_sessions_part_size_check"),
        CheckConstraint(
            "part_count BETWEEN 1 AND 512",
            name="upload_sessions_part_count_check",
        ),
        CheckConstraint(
            "expected_size > (part_count - 1)::bigint * part_size "
            "AND expected_size <= part_count::bigint * part_size",
            name="upload_sessions_geometry_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="RESTRICT"), nullable=False
    )
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    s3_upload_id: Mapped[str | None] = mapped_column(Text)
    expected_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    part_size: Mapped[int] = mapped_column(Integer, nullable=False)
    part_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[UploadStatus] = mapped_column(upload_status_enum, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    completion_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    completion_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    video: Mapped[Video] = relationship()


class VideoProcessingJob(Base):
    __tablename__ = "video_processing_jobs"
    __table_args__ = (CheckConstraint("attempt >= 1", name="video_processing_jobs_attempt_check"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="RESTRICT"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[JobStatus] = mapped_column(job_status_enum, nullable=False)
    failure_class: Mapped[FailureClass | None] = mapped_column(failure_class_enum)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_detail: Mapped[str | None] = mapped_column(Text)
    locked_by: Mapped[str | None] = mapped_column(Text)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    video: Mapped[Video] = relationship()


class PlaybackSession(Base):
    __tablename__ = "playback_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="RESTRICT"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    moderation_access: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    video: Mapped[Video] = relationship()


class VideoViewDedupe(Base):
    __tablename__ = "video_view_dedupe"

    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="RESTRICT"), primary_key=True
    )
    viewer_key: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    last_viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    video: Mapped[Video] = relationship()


class ReactionValue(enum.StrEnum):
    like = "like"
    dislike = "dislike"


class ReportReason(enum.StrEnum):
    spam = "spam"
    harassment = "harassment"
    violence = "violence"
    sexual = "sexual"
    copyright = "copyright"
    other = "other"


class ReportStatus(enum.StrEnum):
    open = "open"
    dismissed = "dismissed"
    action_taken = "action_taken"


class PlaylistVisibility(enum.StrEnum):
    public = "public"
    unlisted = "unlisted"
    private = "private"


reaction_value_enum = ENUM(ReactionValue, name="reaction_value", create_type=False)
report_reason_enum = ENUM(ReportReason, name="report_reason", create_type=False)
report_status_enum = ENUM(ReportStatus, name="report_status", create_type=False)
playlist_visibility_enum = ENUM(PlaylistVisibility, name="playlist_visibility", create_type=False)


class Subscription(Base):
    __tablename__ = "subscriptions"

    subscriber_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="RESTRICT"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VideoReaction(Base):
    __tablename__ = "video_reactions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="RESTRICT"), primary_key=True
    )
    value: Mapped[ReactionValue] = mapped_column(reaction_value_enum, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (
        CheckConstraint(
            "char_length(body) >= 1 AND char_length(body) <= 2000",
            name="comments_body_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("comments.id", ondelete="RESTRICT")
    )
    body: Mapped[str] = mapped_column(String(2000), nullable=False)
    moderation_status: Mapped[ModerationStatus] = mapped_column(
        moderation_status_enum,
        nullable=False,
        default=ModerationStatus.visible,
        server_default="visible",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VideoReport(Base):
    __tablename__ = "video_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="RESTRICT"), nullable=False
    )
    reporter_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[ReportReason] = mapped_column(report_reason_enum, nullable=False)
    details: Mapped[str | None] = mapped_column(String(2000))
    status: Mapped[ReportStatus] = mapped_column(
        report_status_enum, nullable=False, default=ReportStatus.open, server_default="open"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommentReport(Base):
    __tablename__ = "comment_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    comment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("comments.id", ondelete="RESTRICT"), nullable=False
    )
    reporter_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[ReportReason] = mapped_column(report_reason_enum, nullable=False)
    details: Mapped[str | None] = mapped_column(String(2000))
    status: Mapped[ReportStatus] = mapped_column(
        report_status_enum, nullable=False, default=ReportStatus.open, server_default="open"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Playlist(Base):
    __tablename__ = "playlists"
    __table_args__ = (CheckConstraint("char_length(title) >= 1", name="playlists_title_check"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(
        String(2000), nullable=False, default="", server_default=""
    )
    visibility: Mapped[PlaylistVisibility] = mapped_column(
        playlist_visibility_enum,
        nullable=False,
        default=PlaylistVisibility.private,
        server_default="private",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PlaylistItem(Base):
    __tablename__ = "playlist_items"
    __table_args__ = (
        UniqueConstraint("playlist_id", "position", name="playlist_items_position_key"),
    )

    playlist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("playlists.id", ondelete="RESTRICT"), primary_key=True
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="RESTRICT"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ModerationAction(Base):
    __tablename__ = "moderation_actions"
    __table_args__ = (
        CheckConstraint(
            "action IN ('hide_video', 'restore_video', 'dismiss_report', 'change_role', "
            "'deactivate_user', 'reactivate_user', 'view_private_video', 'hide_comment', "
            "'restore_comment')",
            name="moderation_actions_action_check",
        ),
        CheckConstraint(
            "target_type IN ('video', 'report', 'user', 'comment')",
            name="moderation_actions_target_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
