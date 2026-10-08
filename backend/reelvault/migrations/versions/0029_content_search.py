"""Timed subtitle cues and a trigram full-text index."""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("subtitle_tracks", sa.Column("generated", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("subtitle_tracks", sa.Column("source_signature", sa.JSON()))
    op.create_table(
        "subtitle_cues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("track_id", sa.String(32), sa.ForeignKey("subtitle_tracks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("start", sa.Float(), nullable=False),
        sa.Column("end", sa.Float(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.CheckConstraint("start >= 0 AND end > start", name="subtitle_cue_time"),
    )
    op.create_index("ix_subtitle_cues_track_id", "subtitle_cues", ["track_id"])
    op.execute("CREATE VIRTUAL TABLE subtitle_search USING fts5(text, content='subtitle_cues', content_rowid='id', tokenize='trigram')")
    op.execute("""CREATE TRIGGER subtitle_cues_ai AFTER INSERT ON subtitle_cues BEGIN
      INSERT INTO subtitle_search(rowid,text) VALUES(new.id,new.text); END""")
    op.execute("""CREATE TRIGGER subtitle_cues_ad AFTER DELETE ON subtitle_cues BEGIN
      INSERT INTO subtitle_search(subtitle_search,rowid,text) VALUES('delete',old.id,old.text); END""")
    op.execute("""CREATE TRIGGER subtitle_cues_au AFTER UPDATE ON subtitle_cues BEGIN
      INSERT INTO subtitle_search(subtitle_search,rowid,text) VALUES('delete',old.id,old.text);
      INSERT INTO subtitle_search(rowid,text) VALUES(new.id,new.text); END""")


def downgrade() -> None:
    for name in ("subtitle_cues_ai", "subtitle_cues_ad", "subtitle_cues_au"):
        op.execute(f"DROP TRIGGER {name}")
    op.execute("DROP TABLE subtitle_search")
    op.drop_table("subtitle_cues")
    op.drop_column("subtitle_tracks", "source_signature")
    op.drop_column("subtitle_tracks", "generated")
