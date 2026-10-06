"""Persist scene-change candidates and automatic chapters per source version."""

from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scene_analyses",
        sa.Column(
            "video_id",
            sa.String(32),
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("asset_version", sa.Integer(), nullable=False),
        sa.Column("signature", sa.JSON(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("min_interval", sa.Float(), nullable=False),
        sa.Column("duration", sa.Float(), nullable=False),
        sa.Column("cuts", sa.JSON(), nullable=False),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("scene_analyses")
