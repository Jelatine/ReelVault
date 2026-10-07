"""Versioned full-file and multi-frame video fingerprints."""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_fingerprints",
        sa.Column(
            "video_id",
            sa.String(32),
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("asset_version", sa.Integer(), nullable=False),
        sa.Column("signature", sa.JSON(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("visual", sa.JSON(), nullable=True),
        sa.Column("visual_error", sa.Text(), nullable=True),
        sa.Column("algorithm", sa.Integer(), nullable=False),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_video_fingerprints_sha256", "video_fingerprints", ["sha256"])
    op.create_table(
        "duplicate_matches",
        sa.Column("left_hash", sa.String(64), primary_key=True),
        sa.Column("right_hash", sa.String(64), primary_key=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("algorithm", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("duplicate_matches")
    op.drop_table("video_fingerprints")
