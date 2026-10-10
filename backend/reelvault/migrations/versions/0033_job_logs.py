"""Per-job event log shown in the task center."""
import sqlalchemy as sa
from alembic import op

revision = '0033'
down_revision = '0032'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('job_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('job_id', sa.String(32), sa.ForeignKey('jobs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('level', sa.String(8), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False))
    op.create_index('ix_job_logs_job_id', 'job_logs', ['job_id'])


def downgrade() -> None:
    op.drop_table('job_logs')
