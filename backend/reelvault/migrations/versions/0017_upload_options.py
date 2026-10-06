"""Persist resumable folder upload paths and tags."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("uploads", sa.Column("relative_path", sa.String(2048), nullable=True))
    op.add_column("uploads", sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("uploads", "tags")
    op.drop_column("uploads", "relative_path")
