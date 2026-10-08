"""Source-aware CLIP frame vectors, stored in ordinary portable SQLite tables."""
import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "video_vector_indexes",
        sa.Column("video_id", sa.String(32), sa.ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("signature", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("generation", sa.String(32), nullable=False),
        sa.Column("interval", sa.Float(), nullable=False),
        sa.Column("frames", sa.Integer(), nullable=False),
        sa.Column("indexed_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "vector_frames",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_id", sa.String(32), sa.ForeignKey("video_vector_indexes.video_id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.Float(), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=False),
        sa.UniqueConstraint("video_id", "ordinal"),
        sa.CheckConstraint("timestamp >= 0 AND ordinal >= 0 AND ordinal < 1000"),
        sa.CheckConstraint("length(embedding) = 2048"),
    )
    op.create_index("ix_vector_frames_video_id", "vector_frames", ["video_id"])

def downgrade() -> None:
    op.drop_table("vector_frames")
    op.drop_table("video_vector_indexes")
