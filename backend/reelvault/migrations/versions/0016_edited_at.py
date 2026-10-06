"""Persist edit completion time independently of metadata updates."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("edited_at", sa.DateTime(), nullable=True))
    op.execute("""
        UPDATE videos SET edited_at = COALESCE(
            (SELECT MAX(finished_at) FROM jobs
             WHERE kind = 'edit' AND status = 'succeeded' AND result_video_id = videos.id),
            updated_at, created_at)
        WHERE json_extract(edit_params, '$.op') IS NOT NULL
    """)
    op.create_index("ix_videos_edited_at", "videos", ["edited_at"])


def downgrade() -> None:
    op.drop_index("ix_videos_edited_at", table_name="videos")
    op.drop_column("videos", "edited_at")
