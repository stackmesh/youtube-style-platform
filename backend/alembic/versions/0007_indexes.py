"""Index comment replies by parent.

Revision ID: 0007_indexes
Revises: 0006_social
Create Date: 2026-11-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_indexes"
down_revision: str | None = "0006_social"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "comments_parent_idx",
        "comments",
        ["parent_id", "created_at", "id"],
        postgresql_where=sa.text("parent_id IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("comments_parent_idx", table_name="comments")
