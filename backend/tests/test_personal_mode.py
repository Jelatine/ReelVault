from __future__ import annotations

from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from reelvault.backup import create_backup, restore_backup
from reelvault.config import Settings
from reelvault.db import make_engine
from reelvault.migrate import alembic_config, upgrade
from reelvault.models import Video


def test_sharing_endpoints_removed(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert not any(path.startswith(("/api/shares", "/api/public/shares")) for path in paths)
    for method, path in (
        (client.get, "/api/shares"),
        (client.post, "/api/shares"),
        (client.delete, "/api/shares/old-link"),
        (client.get, "/api/public/shares/old-token"),
        (client.post, "/api/public/shares/old-token/unlock"),
        (client.get, "/api/public/shares/old-token/videos/old-video/stream"),
    ):
        assert method(path).status_code in (404, 405)


def test_legacy_sharing_removed_on_upgrade_and_backup_restore(settings, tmp_path):
    settings.ensure_dirs()
    engine = make_engine(settings.db_path)
    command.upgrade(alembic_config(str(engine.url)), "0034")
    media = settings.library_dir / "personal.mp4"
    media.write_bytes(b"original personal video")
    with Session(engine) as db:
        db.add(Video(title="Personal video", file_path="library/personal.mp4", status="ready"))
        db.commit()
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO users (id, username, password_hash, created_at, password_changed_at) "
                "VALUES (1, 'owner', 'hash', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        db.execute(
            text(
                "INSERT INTO collections (id, name, description, created_at) "
                "VALUES (1, 'Personal collection', '', CURRENT_TIMESTAMP)"
            )
        )
        db.execute(
            text(
                "INSERT INTO share_links "
                "(id, owner_id, token, collection_id, allow_download, created_at, expires_at) "
                "VALUES ('old-link', 1, 'old-token', 1, 0, CURRENT_TIMESTAMP, '2099-01-01')"
            )
        )
        db.execute(
            text(
                "INSERT INTO share_grants (token_hash, share_id, expires_at) "
                "VALUES ('old-grant', 'old-link', '2099-01-01')"
            )
        )
    archive = create_backup(settings)
    upgrade(engine)

    def assert_personal_database(db_engine):
        tables = inspect(db_engine).get_table_names()
        assert "share_links" not in tables and "share_grants" not in tables
        with db_engine.connect() as db:
            assert db.execute(text("SELECT username FROM users")).scalar_one() == "owner"
            assert (
                db.execute(text("SELECT name FROM collections")).scalar_one()
                == "Personal collection"
            )
            assert db.execute(text("SELECT title FROM videos")).scalar_one() == "Personal video"

    assert_personal_database(engine)
    # Downgrade restores the schema, never old credentials or links.
    command.downgrade(alembic_config(str(engine.url)), "0034")
    with engine.connect() as db:
        assert db.execute(text("SELECT COUNT(*) FROM share_links")).scalar_one() == 0
        assert db.execute(text("SELECT COUNT(*) FROM share_grants")).scalar_one() == 0
    upgrade(engine)
    assert_personal_database(engine)
    engine.dispose()
    target = Settings(data_dir=tmp_path / "restored-personal", update_check=False)
    target.ensure_dirs()
    (target.library_dir / media.name).write_bytes(media.read_bytes())
    restore_backup(archive, target)
    restored = make_engine(target.db_path)
    try:
        assert_personal_database(restored)
        assert (target.library_dir / media.name).read_bytes() == b"original personal video"
    finally:
        restored.dispose()
