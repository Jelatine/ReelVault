"""Source-specific HLS cache packages."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hls_packages",
        sa.Column(
            "video_id",
            sa.String(32),
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("generation", sa.String(32), nullable=False),
        sa.Column("signature", sa.JSON(), nullable=False),
        sa.Column("renditions", sa.JSON(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("hls_packages")
