"""Job scheduling controls and estimated remaining time."""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("priority", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("jobs", sa.Column("eta_seconds", sa.Float(), nullable=True))
    op.add_column("jobs", sa.Column("retry_of", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "retry_of")
    op.drop_column("jobs", "eta_seconds")
    op.drop_column("jobs", "priority")
