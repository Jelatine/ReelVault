"""Remember imported paths even after videos are purged."""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("import_sources", sa.Column("path", sa.Text(), primary_key=True),
                    sa.Column("imported_at", sa.DateTime(), nullable=False))
    op.execute("""INSERT OR IGNORE INTO import_sources (path, imported_at)
        SELECT source_path, created_at FROM videos WHERE source_path IS NOT NULL""")


def downgrade() -> None:
    op.drop_table("import_sources")
