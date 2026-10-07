"""Library metadata corrections and named custom fields."""

import json
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("metadata_overrides", "custom_fields"):
        op.add_column("videos", sa.Column(name, sa.JSON(), nullable=False, server_default="{}"))
    db = op.get_bind()
    for video_id, raw in db.execute(sa.text("SELECT id, meta FROM videos")):
        tags = json.loads(raw) if isinstance(raw, str) else raw or {}
        tags = {key.lower(): value for key, value in tags.items()}
        for key in (
            "com.apple.quicktime.creationdate",
            "creationdate",
            "creation_time",
            "date_time_original",
        ):
            try:
                value = datetime.fromisoformat(str(tags.get(key, "")).replace("Z", "+00:00"))
                value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
            except (ValueError, OverflowError):
                continue
            db.execute(
                sa.text("UPDATE videos SET captured_at=:date WHERE id=:id"),
                {"date": value.replace(tzinfo=None).isoformat(sep=" "), "id": video_id},
            )
            break


def downgrade() -> None:
    op.drop_column("videos", "custom_fields")
    op.drop_column("videos", "metadata_overrides")
