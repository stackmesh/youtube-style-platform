"""Create the video catalogue, tags, and access grants.

Revision ID: 0003_catalogue
Revises: 0002_identity
Create Date: 2026-11-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_catalogue"
down_revision: str | None = "0002_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamp(name: str) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def upgrade() -> None:
    bind = op.get_bind()
    visibility = postgresql.ENUM(
        "public",
        "unlisted",
        "private",
        name="visibility",
        create_type=False,
    )
    processing_status = postgresql.ENUM(
        "draft",
        "uploading",
        "uploaded",
        "processing",
        "ready",
        "failed",
        "abandoned",
        name="processing_status",
        create_type=False,
    )
    moderation_status = postgresql.ENUM(
        "visible",
        "hidden",
        name="moderation_status",
        create_type=False,
    )
    visibility.create(bind, checkfirst=True)
    processing_status.create(bind, checkfirst=True)
    moderation_status.create(bind, checkfirst=True)
    op.create_table(
        "videos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("description", sa.String(5000), nullable=False, server_default=""),
        sa.Column("visibility", visibility, nullable=False, server_default="private"),
        sa.Column("processing_status", processing_status, nullable=False, server_default="draft"),
        sa.Column("moderation_status", moderation_status, nullable=False, server_default="visible"),
        sa.Column("acl_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("view_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dislike_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("original_object_key", sa.Text()),
        sa.Column("hls_master_key", sa.Text()),
        sa.Column("auto_thumbnail_key", sa.Text()),
        sa.Column("custom_thumbnail_key", sa.Text()),
        sa.Column("source_byte_size", sa.BigInteger()),
        sa.Column("source_content_type", sa.Text()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', title || ' ' || description)", persisted=True),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("storage_released_at", sa.DateTime(timezone=True)),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("char_length(title) >= 1", name="videos_title_check"),
        sa.CheckConstraint("acl_version >= 1", name="videos_acl_version_check"),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds > 0",
            name="videos_duration_check",
        ),
        sa.CheckConstraint("width IS NULL OR width > 0", name="videos_width_check"),
        sa.CheckConstraint("height IS NULL OR height > 0", name="videos_height_check"),
        sa.CheckConstraint("view_count >= 0", name="videos_view_count_check"),
        sa.CheckConstraint("like_count >= 0", name="videos_like_count_check"),
        sa.CheckConstraint("dislike_count >= 0", name="videos_dislike_count_check"),
        sa.CheckConstraint("comment_count >= 0", name="videos_comment_count_check"),
        sa.CheckConstraint(
            "source_byte_size IS NULL OR source_byte_size > 0",
            name="videos_source_size_check",
        ),
        sa.CheckConstraint(
            "processing_status <> 'ready' OR published_at IS NOT NULL",
            name="videos_ready_published_check",
        ),
    )
    op.execute(
        """
        CREATE INDEX videos_feed_idx ON videos (published_at DESC, id DESC)
        WHERE processing_status = 'ready'
          AND moderation_status = 'visible'
          AND visibility = 'public'
          AND deleted_at IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX videos_channel_idx ON videos (channel_id, published_at DESC, id DESC)
        WHERE deleted_at IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX videos_cleanup_idx ON videos (deleted_at)
        WHERE deleted_at IS NOT NULL AND storage_released_at IS NULL
        """
    )
    op.create_index("videos_search_idx", "videos", ["search_vector"], postgresql_using="gin")
    op.create_table(
        "video_tags",
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag", sa.String(30), nullable=False),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("video_id", "tag"),
        sa.CheckConstraint("tag ~ '^[a-z0-9][a-z0-9-]{0,29}$'", name="video_tags_format"),
    )
    op.create_index("video_tags_tag_idx", "video_tags", ["tag"])
    op.create_table(
        "video_access_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grantee_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("granted_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        _timestamp("created_at"),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["grantee_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "grantee_user_id <> granted_by_user_id",
            name="video_access_grants_not_self",
        ),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX video_grants_active_uidx
          ON video_access_grants (video_id, grantee_user_id)
          WHERE revoked_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS video_grants_active_uidx")
    op.drop_table("video_access_grants")
    op.drop_index("video_tags_tag_idx", table_name="video_tags")
    op.drop_table("video_tags")
    op.execute("DROP INDEX IF EXISTS videos_search_idx")
    op.execute("DROP INDEX IF EXISTS videos_cleanup_idx")
    op.execute("DROP INDEX IF EXISTS videos_channel_idx")
    op.execute("DROP INDEX IF EXISTS videos_feed_idx")
    op.drop_table("videos")
    postgresql.ENUM(name="moderation_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="processing_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="visibility").drop(op.get_bind(), checkfirst=True)
