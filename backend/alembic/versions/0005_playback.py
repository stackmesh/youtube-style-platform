"""Create playback sessions and view dedupe rows.

Revision ID: 0005_playback
Revises: 0004_uploads
Create Date: 2026-11-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_playback"
down_revision: str | None = "0004_uploads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "playback_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "playback_sessions_user_idx",
        "playback_sessions",
        ["user_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "playback_sessions_expire_idx",
        "playback_sessions",
        ["expires_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "video_view_dedupe",
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("viewer_key", sa.LargeBinary(), nullable=False),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("video_id", "viewer_key", name="video_view_dedupe_pkey"),
    )
    op.create_index("video_view_dedupe_expire_idx", "video_view_dedupe", ["last_viewed_at"])


def downgrade() -> None:
    op.drop_index("video_view_dedupe_expire_idx", table_name="video_view_dedupe")
    op.drop_table("video_view_dedupe")
    op.drop_index("playback_sessions_expire_idx", table_name="playback_sessions")
    op.drop_index("playback_sessions_user_idx", table_name="playback_sessions")
    op.drop_table("playback_sessions")
