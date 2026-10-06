"""Store structured metadata for LUT assets."""

from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("media_assets", sa.Column("meta", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("media_assets", "meta")
