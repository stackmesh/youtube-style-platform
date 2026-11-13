"""Add reactions, comments, subscriptions, playlists, and moderation.

Revision ID: 0006_social
Revises: 0005_playback
Create Date: 2026-11-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_social"
down_revision: str | None = "0005_playback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    reaction_value = postgresql.ENUM("like", "dislike", name="reaction_value", create_type=False)
    report_reason = postgresql.ENUM(
        "spam",
        "harassment",
        "violence",
        "sexual",
        "copyright",
        "other",
        name="report_reason",
        create_type=False,
    )
    report_status = postgresql.ENUM(
        "open", "dismissed", "action_taken", name="report_status", create_type=False
    )
    playlist_visibility = postgresql.ENUM(
        "public", "unlisted", "private", name="playlist_visibility", create_type=False
    )
    moderation_status = postgresql.ENUM(
        "visible", "hidden", name="moderation_status", create_type=False
    )
    reaction_value.create(bind, checkfirst=True)
    report_reason.create(bind, checkfirst=True)
    report_status.create(bind, checkfirst=True)
    playlist_visibility.create(bind, checkfirst=True)
    op.add_column(
        "playback_sessions",
        sa.Column(
            "moderation_access",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_table(
        "subscriptions",
        sa.Column("subscriber_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["subscriber_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("subscriber_user_id", "channel_id", name="subscriptions_pkey"),
    )
    op.create_index("subscriptions_channel_idx", "subscriptions", ["channel_id"])
    op.execute(
        """
        CREATE FUNCTION subscriptions_reject_self() RETURNS trigger AS $$
        BEGIN
          IF NEW.subscriber_user_id = (
            SELECT user_id FROM channels WHERE id = NEW.channel_id
          ) THEN
            RAISE EXCEPTION 'self subscription is not allowed';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER subscriptions_no_self
        BEFORE INSERT ON subscriptions
        FOR EACH ROW EXECUTE FUNCTION subscriptions_reject_self()
        """
    )
    op.create_table(
        "video_reactions",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("value", reaction_value, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_id", "video_id", name="video_reactions_pkey"),
    )
    op.create_table(
        "comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True)),
        sa.Column("body", sa.String(2000), nullable=False),
        sa.Column("moderation_status", moderation_status, nullable=False, server_default="visible"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "char_length(body) >= 1 AND char_length(body) <= 2000",
            name="comments_body_check",
        ),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["parent_id"], ["comments.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "comments_video_idx",
        "comments",
        ["video_id", "created_at", "id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "video_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reporter_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", report_reason, nullable=False),
        sa.Column("details", sa.String(2000)),
        sa.Column("status", report_status, nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reporter_user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "reports_one_open_uidx",
        "video_reports",
        ["video_id", "reporter_user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_table(
        "comment_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("comment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reporter_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", report_reason, nullable=False),
        sa.Column("details", sa.String(2000)),
        sa.Column("status", report_status, nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["comment_id"], ["comments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reporter_user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "comment_reports_one_open_uidx",
        "comment_reports",
        ["comment_id", "reporter_user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_table(
        "playlists",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("description", sa.String(2000), nullable=False, server_default=""),
        sa.Column("visibility", playlist_visibility, nullable=False, server_default="private"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("char_length(title) >= 1", name="playlists_title_check"),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="RESTRICT"),
    )
    op.create_index("playlists_channel_idx", "playlists", ["channel_id", "created_at"])
    op.create_table(
        "playlist_items",
        sa.Column("playlist_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["playlist_id"], ["playlists.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("playlist_id", "video_id", name="playlist_items_pkey"),
        sa.UniqueConstraint("playlist_id", "position", name="playlist_items_position_key"),
    )
    op.create_table(
        "moderation_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "action IN ('hide_video', 'restore_video', 'dismiss_report', 'change_role', "
            "'deactivate_user', 'reactivate_user', 'view_private_video', 'hide_comment', "
            "'restore_comment')",
            name="moderation_actions_action_check",
        ),
        sa.CheckConstraint(
            "target_type IN ('video', 'report', 'user', 'comment')",
            name="moderation_actions_target_check",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "moderation_actions_target_idx",
        "moderation_actions",
        ["target_type", "target_id", "created_at"],
    )
    op.create_index(
        "moderation_actions_actor_idx",
        "moderation_actions",
        ["actor_user_id", "created_at"],
    )
    op.execute(
        """
        CREATE FUNCTION moderation_actions_append_only() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'moderation_actions is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER moderation_actions_no_mutate
        BEFORE UPDATE OR DELETE ON moderation_actions
        FOR EACH ROW EXECUTE FUNCTION moderation_actions_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS moderation_actions_no_mutate ON moderation_actions")
    op.execute("DROP FUNCTION IF EXISTS moderation_actions_append_only()")
    op.drop_index("moderation_actions_actor_idx", table_name="moderation_actions")
    op.drop_index("moderation_actions_target_idx", table_name="moderation_actions")
    op.drop_table("moderation_actions")
    op.drop_table("playlist_items")
    op.drop_index("playlists_channel_idx", table_name="playlists")
    op.drop_table("playlists")
    op.drop_index("comment_reports_one_open_uidx", table_name="comment_reports")
    op.drop_table("comment_reports")
    op.drop_index("reports_one_open_uidx", table_name="video_reports")
    op.drop_table("video_reports")
    op.drop_index("comments_video_idx", table_name="comments")
    op.drop_table("comments")
    op.drop_table("video_reactions")
    op.execute("DROP TRIGGER IF EXISTS subscriptions_no_self ON subscriptions")
    op.execute("DROP FUNCTION IF EXISTS subscriptions_reject_self()")
    op.drop_index("subscriptions_channel_idx", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_column("playback_sessions", "moderation_access")
    bind = op.get_bind()
    postgresql.ENUM(name="playlist_visibility").drop(bind, checkfirst=True)
    postgresql.ENUM(name="report_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="report_reason").drop(bind, checkfirst=True)
    postgresql.ENUM(name="reaction_value").drop(bind, checkfirst=True)
