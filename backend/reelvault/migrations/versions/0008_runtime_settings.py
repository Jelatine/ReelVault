"""Persistent encoder preferences."""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("runtime_settings", sa.Column("key", sa.String(64), primary_key=True),
                    sa.Column("value", sa.JSON(), nullable=False))


def downgrade() -> None:
    op.drop_table("runtime_settings")
