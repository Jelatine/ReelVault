"""External subtitle tracks attached to library videos."""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("subtitle_tracks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("video_id", sa.String(32), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_id", sa.String(32), sa.ForeignKey("media_assets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("language", sa.String(35), nullable=False))
    op.create_index("ix_subtitle_tracks_video_id", "subtitle_tracks", ["video_id"])


def downgrade() -> None:
    op.drop_table("subtitle_tracks")
