"""Encrypted TOTP enrollments and single-use recovery codes."""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "two_factor",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("enabled_at", sa.DateTime()),
        sa.Column("pending_session_id", sa.String(32)),
        sa.Column("pending_expires_at", sa.DateTime()),
        sa.Column("last_counter", sa.BigInteger(), nullable=False),
        sa.Column("recovery_hashes", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("two_factor")
