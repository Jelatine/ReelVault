"""Trigram full-text search with transactional index maintenance."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

SELECT_TEXT = """
    SELECT v.id, v.title, v.description, v.original_name,
      coalesce((SELECT group_concat(t.name, ' ') FROM tags t
        JOIN video_tags vt ON vt.tag_id=t.id WHERE vt.video_id=v.id), '')
    FROM videos v
"""


def upgrade() -> None:
    op.add_column("videos", sa.Column("captured_at", sa.DateTime(), nullable=True))
    op.execute("""CREATE VIRTUAL TABLE video_search USING fts5(
        video_id UNINDEXED, title, description, original_name, tags, tokenize='trigram'
    )""")
    op.execute("INSERT INTO video_search " + SELECT_TEXT)
    op.execute("""CREATE TRIGGER search_video_insert AFTER INSERT ON videos BEGIN
        INSERT INTO video_search (video_id, title, description, original_name, tags)
        VALUES (new.id, new.title, new.description, new.original_name, ''); END""")
    op.execute("""CREATE TRIGGER search_video_update
        AFTER UPDATE OF title, description, original_name ON videos BEGIN
        UPDATE video_search SET title=new.title, description=new.description,
          original_name=new.original_name WHERE video_id=new.id; END""")
    op.execute("""CREATE TRIGGER search_video_delete AFTER DELETE ON videos BEGIN
        DELETE FROM video_search WHERE video_id=old.id; END""")
    for event, ref in (("INSERT", "new"), ("DELETE", "old")):
        op.execute(f"""CREATE TRIGGER search_tag_{event.lower()} AFTER {event} ON video_tags BEGIN
            DELETE FROM video_search WHERE video_id={ref}.video_id;
            INSERT INTO video_search {SELECT_TEXT} WHERE v.id={ref}.video_id; END""")
    op.execute("""CREATE TRIGGER search_tag_rename AFTER UPDATE OF name ON tags BEGIN
        UPDATE video_search SET tags=coalesce((SELECT group_concat(t.name, ' ')
          FROM tags t JOIN video_tags vt ON vt.tag_id=t.id
          WHERE vt.video_id=video_search.video_id), '')
        WHERE video_id IN (SELECT video_id FROM video_tags WHERE tag_id=new.id); END""")


def downgrade() -> None:
    for trigger in ("video_insert", "video_update", "video_delete", "tag_insert", "tag_delete", "tag_rename"):
        op.execute("DROP TRIGGER search_" + trigger)
    op.execute("DROP TABLE video_search")
    with op.batch_alter_table("videos") as batch:
        batch.drop_column("captured_at")
