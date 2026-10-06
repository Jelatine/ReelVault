"""Persistent provenance, independent of removable job records."""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("source_video_id", sa.String(32), nullable=True))
    op.add_column("videos", sa.Column("edit_params", sa.JSON(), nullable=True))
    op.add_column("videos", sa.Column("edit_sources", sa.JSON(), nullable=False, server_default="[]"))
    op.create_index("ix_videos_source_video_id", "videos", ["source_video_id"])
    # Old replacement jobs cannot identify an immutable input version. Do not invent one.
    op.execute("""UPDATE videos SET
        source_video_id=(SELECT json_extract(j.video_ids, '$[0]') FROM jobs j
            WHERE j.result_video_id=videos.id AND j.kind='edit' AND j.status='succeeded'
              AND json_extract(j.video_ids, '$[0]') != videos.id ORDER BY j.created_at DESC LIMIT 1),
        edit_params=(SELECT json_extract(j.params, '$.edit') FROM jobs j
            WHERE j.result_video_id=videos.id AND j.kind='edit' AND j.status='succeeded'
              AND json_extract(j.video_ids, '$[0]') != videos.id ORDER BY j.created_at DESC LIMIT 1)
    """)


def downgrade() -> None:
    op.drop_index("ix_videos_source_video_id", table_name="videos")
    op.drop_column("videos", "edit_sources")
    op.drop_column("videos", "edit_params")
    op.drop_column("videos", "source_video_id")
