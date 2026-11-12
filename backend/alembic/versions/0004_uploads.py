"""Create upload sessions and processing jobs.

Revision ID: 0004_uploads
Revises: 0003_catalogue
Create Date: 2026-11-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_uploads"
down_revision: str | None = "0003_catalogue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    upload_status = postgresql.ENUM(
        "initiated",
        "completing",
        "completed",
        "aborted",
        "expired",
        name="upload_status",
        create_type=False,
    )
    job_status = postgresql.ENUM(
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        name="job_status",
        create_type=False,
    )
    failure_class = postgresql.ENUM(
        "fatal",
        "retryable",
        name="failure_class",
        create_type=False,
    )
    upload_status.create(bind, checkfirst=True)
    job_status.create(bind, checkfirst=True)
    failure_class.create(bind, checkfirst=True)
    op.add_column("videos", sa.Column("source_sha256", sa.String(length=64), nullable=True))
    op.create_check_constraint(
        "videos_source_sha256_check",
        "videos",
        "source_sha256 IS NULL OR source_sha256 ~ '^[a-f0-9]{64}$'",
    )
    op.create_table(
        "upload_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("s3_upload_id", sa.Text()),
        sa.Column("expected_size", sa.BigInteger(), nullable=False),
        sa.Column("part_size", sa.Integer(), nullable=False),
        sa.Column("part_count", sa.Integer(), nullable=False),
        sa.Column("status", upload_status, nullable=False),
        sa.Column("error_code", sa.Text()),
        sa.Column("completion_token", postgresql.UUID(as_uuid=True)),
        sa.Column("completion_started_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("expected_size > 0", name="upload_sessions_size_check"),
        sa.CheckConstraint("part_size = 16777216", name="upload_sessions_part_size_check"),
        sa.CheckConstraint(
            "part_count BETWEEN 1 AND 512",
            name="upload_sessions_part_count_check",
        ),
        sa.CheckConstraint(
            "expected_size > (part_count - 1)::bigint * part_size "
            "AND expected_size <= part_count::bigint * part_size",
            name="upload_sessions_geometry_check",
        ),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX upload_sessions_one_active_uidx
          ON upload_sessions (video_id)
          WHERE status IN ('initiated', 'completing')
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX upload_sessions_s3_upload_uidx
          ON upload_sessions (s3_upload_id)
          WHERE s3_upload_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX upload_sessions_expire_idx
          ON upload_sessions (expires_at)
          WHERE status IN ('initiated', 'completing')
        """
    )
    op.create_table(
        "video_processing_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", job_status, nullable=False),
        sa.Column("failure_class", failure_class),
        sa.Column("error_code", sa.Text()),
        sa.Column("error_detail", sa.Text()),
        sa.Column("locked_by", sa.Text()),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("attempt >= 1", name="video_processing_jobs_attempt_check"),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX jobs_one_active_uidx
          ON video_processing_jobs (video_id)
          WHERE status IN ('queued', 'running')
        """
    )
    op.execute(
        """
        CREATE INDEX jobs_claim_idx
          ON video_processing_jobs (next_run_at, created_at)
          WHERE status = 'queued'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS jobs_claim_idx")
    op.execute("DROP INDEX IF EXISTS jobs_one_active_uidx")
    op.drop_table("video_processing_jobs")
    op.execute("DROP INDEX IF EXISTS upload_sessions_expire_idx")
    op.execute("DROP INDEX IF EXISTS upload_sessions_s3_upload_uidx")
    op.execute("DROP INDEX IF EXISTS upload_sessions_one_active_uidx")
    op.drop_table("upload_sessions")
    op.drop_constraint("videos_source_sha256_check", "videos", type_="check")
    op.drop_column("videos", "source_sha256")
    postgresql.ENUM(name="failure_class").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="job_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="upload_status").drop(op.get_bind(), checkfirst=True)
