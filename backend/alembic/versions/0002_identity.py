"""Create identity, channel, and quota tables.

Revision ID: 0002_identity
Revises: 0001_extensions
Create Date: 2026-11-10

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_identity"
down_revision: str | None = "0001_extensions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamp(name: str) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def _user_id(**extra: object) -> sa.Column:
    return sa.Column(
        "user_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        **extra,
    )


def upgrade() -> None:
    user_role = postgresql.ENUM(
        "USER",
        "MODERATOR",
        "ADMIN",
        name="user_role",
        create_type=False,
    )
    user_role.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", postgresql.CITEXT(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", user_role, nullable=False, server_default="USER"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("session_epoch", sa.Integer(), nullable=False, server_default="1"),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint("session_epoch >= 1", name="users_session_epoch_check"),
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _user_id(nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False, unique=True),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("replaced_by_id", postgresql.UUID(as_uuid=True)),
        _timestamp("created_at"),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"],
            ["refresh_tokens.id"],
            name="refresh_tokens_replaced_by_id_fkey",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("refresh_tokens_family_id_idx", "refresh_tokens", ["family_id"])
    op.create_index("refresh_tokens_user_id_idx", "refresh_tokens", ["user_id"])
    op.create_table(
        "channels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _user_id(nullable=False, unique=True),
        sa.Column("handle", postgresql.CITEXT(), nullable=False, unique=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.String(2000), nullable=False, server_default=""),
        sa.Column("avatar_object_key", sa.Text()),
        sa.Column("subscriber_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', name || ' ' || handle::text)", persisted=True),
            nullable=False,
        ),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint("handle::text ~ '^[a-z0-9_]{3,30}$'", name="channels_handle_format"),
        sa.CheckConstraint("subscriber_count >= 0", name="channels_subscriber_count_check"),
    )
    op.create_index("channels_search_idx", "channels", ["search_vector"], postgresql_using="gin")
    op.execute(
        "CREATE INDEX channels_handle_trgm ON channels USING gin ((handle::text) gin_trgm_ops)"
    )
    op.execute("CREATE INDEX channels_name_trgm ON channels USING gin (name gin_trgm_ops)")
    op.create_table(
        "user_storage_quotas",
        _user_id(primary_key=True),
        sa.Column("bytes_committed", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_reserved", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_limit", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("bytes_committed >= 0", name="user_storage_quotas_committed_check"),
        sa.CheckConstraint("bytes_reserved >= 0", name="user_storage_quotas_reserved_check"),
        sa.CheckConstraint("bytes_limit > 0", name="user_storage_quotas_limit_check"),
    )


def downgrade() -> None:
    op.drop_table("user_storage_quotas")
    op.execute("DROP INDEX IF EXISTS channels_name_trgm")
    op.execute("DROP INDEX IF EXISTS channels_handle_trgm")
    op.drop_index("channels_search_idx", table_name="channels")
    op.drop_table("channels")
    op.drop_index("refresh_tokens_user_id_idx", table_name="refresh_tokens")
    op.drop_index("refresh_tokens_family_id_idx", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
    postgresql.ENUM(name="user_role").drop(op.get_bind(), checkfirst=True)
