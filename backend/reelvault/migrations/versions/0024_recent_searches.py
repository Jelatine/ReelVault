"""Personal deduplicated recent searches."""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recent_searches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("query", sa.String(512), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "query"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_recent_searches_user_id", "recent_searches", ["user_id"])


def downgrade() -> None:
    op.drop_table("recent_searches")
