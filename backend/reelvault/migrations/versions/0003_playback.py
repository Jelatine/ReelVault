"""Per-user playback history."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "playback",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("video_id", sa.String(32), sa.ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("position", sa.Float(), nullable=False, server_default="0"),
        sa.Column("play_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_played_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("playback")
