"""Add full spelling and initials to the transactional title search index."""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def rebuild(*, pinyin: bool) -> None:
    for name in ("video_insert", "video_update", "video_delete", "tag_insert", "tag_delete", "tag_rename"):
        op.execute("DROP TRIGGER search_" + name)
    op.execute("DROP TABLE video_search")
    extra_columns = ", title_pinyin, title_initials" if pinyin else ""
    extra_values = ", reelvault_pinyin(v.title), reelvault_initials(v.title)" if pinyin else ""
    op.execute(f"""CREATE VIRTUAL TABLE video_search USING fts5(
      video_id UNINDEXED, title, description, original_name, tags{extra_columns}, tokenize='trigram'
    )""")
    tags = """coalesce((SELECT group_concat(t.name, ' ') FROM tags t
      JOIN video_tags vt ON vt.tag_id=t.id WHERE vt.video_id=v.id), '')"""
    op.execute(f"""INSERT INTO video_search SELECT v.id, v.title, v.description, v.original_name,
      {tags}{extra_values} FROM videos v""")
    new_values = ", reelvault_pinyin(new.title), reelvault_initials(new.title)" if pinyin else ""
    op.execute(f"""CREATE TRIGGER search_video_insert AFTER INSERT ON videos BEGIN
      INSERT INTO video_search (video_id, title, description, original_name, tags{extra_columns})
      VALUES (new.id, new.title, new.description, new.original_name, ''{new_values}); END""")
    updates = ", title_pinyin=reelvault_pinyin(new.title), title_initials=reelvault_initials(new.title)" if pinyin else ""
    op.execute(f"""CREATE TRIGGER search_video_update
      AFTER UPDATE OF title, description, original_name ON videos BEGIN
      UPDATE video_search SET title=new.title, description=new.description,
        original_name=new.original_name{updates} WHERE video_id=new.id; END""")
    op.execute("""CREATE TRIGGER search_video_delete AFTER DELETE ON videos BEGIN
      DELETE FROM video_search WHERE video_id=old.id; END""")
    for event, ref in (("INSERT", "new"), ("DELETE", "old")):
        op.execute(f"""CREATE TRIGGER search_tag_{event.lower()} AFTER {event} ON video_tags BEGIN
          UPDATE video_search SET tags=coalesce((SELECT group_concat(t.name, ' ') FROM tags t
            JOIN video_tags vt ON vt.tag_id=t.id WHERE vt.video_id={ref}.video_id), '')
          WHERE video_id={ref}.video_id; END""")
    op.execute("""CREATE TRIGGER search_tag_rename AFTER UPDATE OF name ON tags BEGIN
      UPDATE video_search SET tags=coalesce((SELECT group_concat(t.name, ' ') FROM tags t
        JOIN video_tags vt ON vt.tag_id=t.id WHERE vt.video_id=video_search.video_id), '')
      WHERE video_id IN (SELECT video_id FROM video_tags WHERE tag_id=new.id); END""")


def upgrade() -> None:
    rebuild(pinyin=True)


def downgrade() -> None:
    rebuild(pinyin=False)
