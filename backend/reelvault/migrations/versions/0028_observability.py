"""Persistent audit history and bounded cumulative job metrics."""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("peer", sa.String(64), nullable=False),
        sa.Column("target", sa.String(64)),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_audit_events_event", "audit_events", ["event"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_table(
        "job_metrics",
        sa.Column("kind", sa.String(32), primary_key=True),
        sa.Column("status", sa.String(16), primary_key=True),
        sa.Column("completed", sa.BigInteger(), nullable=False),
        sa.Column("duration_count", sa.BigInteger(), nullable=False),
        sa.Column("duration_sum", sa.Float(), nullable=False),
        sa.Column("buckets", sa.JSON(), nullable=False),
    )
    op.add_column("jobs", sa.Column("metrics_recorded", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("jobs", "metrics_recorded")
    op.drop_table("job_metrics")
    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_event", table_name="audit_events")
    op.drop_table("audit_events")
