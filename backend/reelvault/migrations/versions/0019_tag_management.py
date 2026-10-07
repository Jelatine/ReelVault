"""Tag colors and groups without rebuilding referenced tag rows."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tag_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
    )
    op.add_column("tags", sa.Column("color", sa.String(7), nullable=True))
    # A nullable inline reference is supported by SQLite ADD COLUMN. Rebuilding
    # tags would cascade-delete video_tags and lose existing associations.
    op.execute(
        "ALTER TABLE tags ADD COLUMN group_id INTEGER REFERENCES tag_groups(id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tags DROP COLUMN group_id")
    op.execute("ALTER TABLE tags DROP COLUMN color")
    op.drop_table("tag_groups")
