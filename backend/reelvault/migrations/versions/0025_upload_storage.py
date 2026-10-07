"""Keep a stable target location across resumable uploads."""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("uploads", sa.Column("storage_id", sa.String(32), nullable=False, server_default="local"))


def downgrade() -> None:
    op.drop_column("uploads", "storage_id")
