import shutil
import time

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from reelvault.db import make_engine
from reelvault.main import create_app
from reelvault.migrate import alembic_config, upgrade
from reelvault.models import ImportSource, Upload, Video

from .conftest import HEADERS, login, wait_ready


def test_watch_real_file_stability_restart_and_source_independence(settings, samples, tmp_path):
    root = tmp_path / "incoming"
    root.mkdir()
    settings.import_dir = root
    path = root / "new.mp4"
    with TestClient(create_app(settings), headers=HEADERS) as client:
        login(client)
        importer = client.app.state.importer
        importer.interval = .1
        assert client.get("/api/system/import-watch").json()["enabled"] is False
        shutil.copy2(samples["a"], path)
        assert client.get("/api/videos").json()["total"] == 0
        assert client.put("/api/system/import-watch", json={"enabled": True,
            "stable_seconds": 2}).status_code == 200
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and str(path) not in importer._seen:
            time.sleep(.05)
        assert str(path) in importer._seen
        # A changing file starts the stability timer again.
        time.sleep(.3)
        path.touch()
        time.sleep(.4)
        assert client.get("/api/videos").json()["total"] == 0
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            videos = client.get("/api/videos").json()["items"]
            if videos:
                break
            time.sleep(.1)
        assert len(videos) == 1
        video = wait_ready(client, videos[0]["id"])
        with client.app.state.sessionmaker() as db:
            stored = settings.data_dir / db.get(Video, video["id"]).file_path
            original = stored.read_bytes()
        path.write_bytes(b"source changed")
        assert stored.read_bytes() == original
        assert client.post("/api/system/import").json()["imported"] == 0
        # Purging a video must not cause the watch to resurrect it.
        client.delete(f"/api/videos/{video['id']}")
        assert client.delete(f"/api/videos/{video['id']}?permanent=true").status_code == 200
    with TestClient(create_app(settings), headers=HEADERS) as client:
        login(client)
        status = client.get("/api/system/import-watch").json()
        assert status["enabled"] is True and status["stable_seconds"] == 2
        assert client.post("/api/system/import").json()["imported"] == 0
        assert client.get("/api/videos").json()["total"] == 0
        assert client.put("/api/system/import-watch", json={"enabled": False}).status_code == 200
        shutil.copy2(samples["b"], root / "second.mp4")
        assert client.post("/api/system/import").json()["imported"] == 1
        assert client.post("/api/system/import").json()["imported"] == 0


def test_import_skips_symlinks_data_and_changed_copy(settings, samples, tmp_path, monkeypatch):
    settings.import_dir = tmp_path
    with TestClient(create_app(settings), headers=HEADERS) as client:
        login(client)
        shutil.copy2(samples["a"], tmp_path / "changed.mp4")
        (tmp_path / "link.mp4").symlink_to(samples["a"])
        (tmp_path / "linked-directory").symlink_to(samples["a"].parent, target_is_directory=True)
        shutil.copy2(samples["a"], settings.data_dir / "internal.mp4")
        original_copy = shutil.copy2

        def copy_and_mutate(src, dst, **kwargs):
            result = original_copy(src, dst, **kwargs)
            src.write_bytes(b"changed during copy")
            return result

        with monkeypatch.context() as m:
            m.setattr("reelvault.importer.shutil.copy2", copy_and_mutate)
            assert client.post("/api/system/import").json()["imported"] == 0
        assert list(settings.tmp_dir.glob("import-*")) == []
        assert client.get("/api/videos").json()["total"] == 0
        original_copy(samples["a"], tmp_path / "changed.mp4")
        assert client.post("/api/system/import").json()["imported"] == 1
        assert client.post("/api/system/import").json()["imported"] == 0


def test_watch_auth_invalid_config_and_range(anon, settings):
    assert anon.get("/api/system/import-watch").status_code == 401
    assert anon.put("/api/system/import-watch", json={"enabled": True}).status_code == 401
    login(anon)
    assert anon.put("/api/system/import-watch", json={"enabled": True}).status_code == 400
    assert anon.post("/api/system/import").status_code == 400
    assert anon.put("/api/system/import-watch", json={"enabled": False,
        "stable_seconds": 1}).status_code == 422
    settings.import_dir = settings.data_dir
    assert anon.put("/api/system/import-watch", json={"enabled": True}).status_code == 400
    assert anon.post("/api/system/import").status_code == 400


def test_upload_and_import_migrations_preserve_legacy_rows(tmp_path):
    from alembic import command
    from sqlalchemy.orm import Session

    engine = make_engine(tmp_path / "legacy.db")
    cfg = alembic_config(str(engine.url))
    upgrade(engine)
    with Session(engine) as db:
        db.add(Upload(id="u" * 32, filename="a.mp4", size=10, received=4))
        db.add(Video(id="v" * 32, title="legacy", original_name="old.mp4",
            file_path="library/old.mp4", source_path="/incoming/old.mp4"))
        db.commit()
    command.downgrade(cfg, "0016")
    upgrade(engine)
    with Session(engine) as db:
        upload = db.get(Upload, "u" * 32)
        assert upload.received == 4 and upload.tags == [] and upload.relative_path is None
        assert db.scalar(select(ImportSource.path)) == "/incoming/old.mp4"
    command.downgrade(cfg, "0016")
    with engine.connect() as db:
        assert db.execute(text("SELECT received FROM uploads")).scalar() == 4
        assert db.execute(text("SELECT title FROM videos")).scalar() == "legacy"
    engine.dispose()
