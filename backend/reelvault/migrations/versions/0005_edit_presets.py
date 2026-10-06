"""Reusable editing parameters with initial compression presets."""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    presets = op.create_table(
        "edit_presets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("edit", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.bulk_insert(presets, [
        {"name": "720p 微信发送", "edit": {"op": "compress", "codec": "h264", "resolution": 720, "max_fps": 30, "quality": "medium"}, "created_at": datetime.now(UTC).replace(tzinfo=None)},
        {"name": "H.265 归档", "edit": {"op": "compress", "codec": "h265", "quality": "high", "preset": "slow"}, "created_at": datetime.now(UTC).replace(tzinfo=None)},
    ])


def downgrade() -> None:
    op.drop_table("edit_presets")
