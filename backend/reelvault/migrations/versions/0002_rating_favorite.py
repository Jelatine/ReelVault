"""Video ratings and favorites, including defaults for existing libraries."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("rating", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("videos", sa.Column("favorite", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("videos") as batch:
        batch.drop_column("favorite")
        batch.drop_column("rating")
