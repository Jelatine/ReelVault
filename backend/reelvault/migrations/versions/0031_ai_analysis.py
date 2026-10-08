"""Reviewed scene suggestions and source-aware face groups."""
import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("ai_analyses",
        sa.Column("video_id", sa.String(32), sa.ForeignKey("video_vector_indexes.video_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("generation", sa.String(32), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("face_model", sa.String(80)),
        sa.Column("suggestions", sa.JSON(), nullable=False),
        sa.Column("analyzed_at", sa.DateTime(), nullable=False))
    op.create_table("face_groups",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_table("face_observations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("video_id", sa.String(32), sa.ForeignKey("ai_analyses.video_id", ondelete="CASCADE"), nullable=False),
        sa.Column("frame_id", sa.Integer(), sa.ForeignKey("vector_frames.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("box", sa.JSON(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=False),
        sa.Column("group_id", sa.String(32), sa.ForeignKey("face_groups.id", ondelete="SET NULL")),
        sa.Column("manual", sa.Boolean(), nullable=False),
        sa.Column("ignored", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("frame_id", "ordinal"),
        sa.CheckConstraint("ordinal >= 0 AND ordinal < 16"),
        sa.CheckConstraint("length(embedding) = 512"))
    for column in ("video_id", "frame_id", "group_id"):
        op.create_index("ix_face_observations_" + column, "face_observations", [column])

def downgrade() -> None:
    op.drop_table("face_observations")
    op.drop_table("face_groups")
    op.drop_table("ai_analyses")
