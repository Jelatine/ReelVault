"""Expiring read-only video and collection shares with isolated password grants."""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "share_links",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token", sa.String(64), nullable=False, unique=True),
        sa.Column("video_id", sa.String(32), sa.ForeignKey("videos.id", ondelete="CASCADE")),
        sa.Column("collection_id", sa.Integer(), sa.ForeignKey("collections.id", ondelete="CASCADE")),
        sa.Column("password_hash", sa.String(255)),
        sa.Column("allow_download", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("(video_id IS NULL) != (collection_id IS NULL)", name="share_one_target"),
    )
    op.create_index("ix_share_links_owner_id", "share_links", ["owner_id"])
    op.create_table(
        "share_grants",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("share_id", sa.String(32), sa.ForeignKey("share_links.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_share_grants_share_id", "share_grants", ["share_id"])


def downgrade() -> None:
    op.drop_table("share_grants")
    op.drop_table("share_links")
