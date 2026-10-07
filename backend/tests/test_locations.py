from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from reelvault.library import abs_path, rel_path
from reelvault.locations import MARKER, LocationUnavailable, library_root, register_root
from reelvault.maintenance import purge_expired_trash
from reelvault.models import Video, utcnow


def registered(settings, tmp_path):
    root = tmp_path / "external-drive"
    root.mkdir()
    location_id, entry = register_root(settings, root, "旅行硬盘")
    settings.storage_locations[location_id] = entry
    return location_id, root


def test_fixed_identity_path_roundtrip_and_mount_change(settings, tmp_path):
    location_id, root = registered(settings, tmp_path)
    media = library_root(settings, location_id) / "original.mp4"
    media.write_bytes(b"original")
    encoded = rel_path(settings, media)
    assert encoded == f"volumes/{location_id}/original.mp4"
    assert abs_path(settings, encoded).read_bytes() == b"original"
    moved = tmp_path / "other-mount"
    root.rename(moved)
    settings.storage_locations[location_id].path = moved
    assert abs_path(settings, encoded).read_bytes() == b"original"
    assert rel_path(settings, abs_path(settings, encoded)) == encoded
    settings.ensure_dirs()
    local = settings.library_dir / "legacy.mp4"
    local.write_bytes(b"legacy")
    assert abs_path(settings, "library/legacy.mp4") == local
    assert rel_path(settings, local) == "library/legacy.mp4"


def test_disconnected_or_replaced_drive_never_created_or_used(settings, tmp_path):
    location_id, root = registered(settings, tmp_path)
    root.rename(tmp_path / "offline-drive")
    with pytest.raises(LocationUnavailable):
        abs_path(settings, f"volumes/{location_id}/new.mp4")
    assert not root.exists()
    root.mkdir()
    with pytest.raises(LocationUnavailable):
        library_root(settings, location_id)
    assert list(root.iterdir()) == []
    (root / MARKER).write_text(json.dumps({"version": 1, "id": "a" * 32}))
    (root / "library").mkdir()
    with pytest.raises(LocationUnavailable):
        library_root(settings, location_id)
    assert not (root / "library" / "new.mp4").exists()


def test_registration_rejects_overlap_existing_files_and_symlinks(settings, tmp_path):
    settings.ensure_dirs()
    for path in (settings.data_dir, settings.library_dir, tmp_path):
        with pytest.raises(ValueError):
            register_root(settings, path, "overlap")
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    old = occupied / "important.mp4"
    old.write_bytes(b"keep")
    with pytest.raises(ValueError):
        register_root(settings, occupied, "occupied")
    assert old.read_bytes() == b"keep"
    assert not (occupied / MARKER).exists()
    with pytest.raises(OSError):
        register_root(settings, tmp_path / "missing-drive", "missing")
    assert not (tmp_path / "missing-drive").exists()
    linked = tmp_path / "linked"
    linked.symlink_to(occupied, target_is_directory=True)
    with pytest.raises(OSError):
        register_root(settings, linked, "linked")


def test_reconnect_keeps_identity_and_rejects_tampered_marker(settings, tmp_path):
    location_id, root = registered(settings, tmp_path)
    settings.storage_locations.clear()
    same_id, entry = register_root(settings, root, "reconnected")
    assert same_id == location_id and entry.name == "reconnected"
    settings.storage_locations[same_id] = entry
    marker = root / MARKER
    marker.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"version": 1, "id": same_id}))
    marker.symlink_to(outside)
    with pytest.raises(LocationUnavailable):
        library_root(settings, same_id)
    marker.unlink()
    marker.write_text(json.dumps({"version": True, "id": same_id}))
    with pytest.raises(LocationUnavailable):
        library_root(settings, same_id)


def test_resolver_rejects_traversal_and_symlink_escape(settings, tmp_path):
    location_id, root = registered(settings, tmp_path)
    settings.ensure_dirs()
    for value in (
        "../private.mp4",
        "/etc/passwd",
        "library/../outside.mp4",
        "library\\outside.mp4",
        "volumes/invalid/file.mp4",
        f"volumes/{location_id}/../outside.mp4",
    ):
        with pytest.raises(ValueError):
            abs_path(settings, value)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"private")
    link = root / "library" / "link.mp4"
    link.symlink_to(outside)
    with pytest.raises(ValueError):
        abs_path(settings, f"volumes/{location_id}/link.mp4")
    with pytest.raises(ValueError):
        rel_path(settings, link)
    with pytest.raises(ValueError):
        rel_path(settings, outside)


def test_offline_storage_retains_trash_and_reports_http_503(client, settings, tmp_path):
    location_id, root = registered(settings, tmp_path)
    media = root / "library" / "offline.mp4"
    media.write_bytes(b"original")
    with client.app.state.sessionmaker() as db:
        video = Video(title="external", file_path=rel_path(settings, media), status="ready", size=8)
        db.add(video)
        db.commit()
        video_id = video.id
    assert client.get(f"/api/videos/{video_id}/download").content == b"original"
    root.rename(tmp_path / "disconnected")
    response = client.get(f"/api/videos/{video_id}/download")
    assert response.status_code == 503
    assert response.json()["code"] == "storage_location_unavailable"
    with client.app.state.sessionmaker() as db:
        video = db.get(Video, video_id)
        video.deleted_at = utcnow() - timedelta(days=31)
        db.commit()
    assert purge_expired_trash(settings, client.app.state.sessionmaker) == 0
    with client.app.state.sessionmaker() as db:
        assert db.scalar(select(Video.id).where(Video.id == video_id)) == video_id
    assert (tmp_path / "disconnected" / "library" / "offline.mp4").read_bytes() == b"original"


def test_registry_api_persists_reconnects_and_never_deletes_media(client, settings, tmp_path):
    root = tmp_path / "catalog-drive"
    root.mkdir()
    response = client.post("/api/system/locations", json={"name": "存储一", "path": str(root)})
    assert response.status_code == 200, response.text
    key = next(item["id"] for item in response.json()["items"] if item["id"] != "local")
    assert (
        client.put("/api/system/locations/default", json={"location_id": key}).json()["default_id"]
        == key
    )
    from reelvault.models import RuntimeSetting

    with client.app.state.sessionmaker() as db:
        saved = db.get(RuntimeSetting, "storage_locations")
        assert saved.value["storage_default"] == key
        assert saved.value["storage_locations"][key]["path"] == str(root)
    moved = tmp_path / "new-mount"
    root.rename(moved)
    offline = next(
        item for item in client.get("/api/system/locations").json()["items"] if item["id"] == key
    )
    assert not offline["available"] and offline["free"] is None
    bad = tmp_path / "bad-drive"
    bad.mkdir()
    assert (
        client.patch(
            f"/api/system/locations/{key}", json={"name": "changed", "path": str(bad)}
        ).status_code
        == 400
    )
    assert settings.storage_locations[key].path == root
    response = client.patch(
        f"/api/system/locations/{key}", json={"name": "reconnected", "path": str(moved)}
    )
    assert response.status_code == 200
    assert next(item for item in response.json()["items"] if item["id"] == key)["available"]
    media = moved / "library" / "retained.mp4"
    media.write_bytes(b"keep")
    with client.app.state.sessionmaker() as db:
        video = Video(
            title="retained",
            file_path=rel_path(settings, media),
            status="ready",
            deleted_at=utcnow(),
        )
        db.add(video)
        db.commit()
        video_id = video.id
    assert client.delete(f"/api/system/locations/{key}").status_code == 409
    assert media.read_bytes() == b"keep"
    with client.app.state.sessionmaker() as db:
        db.delete(db.get(Video, video_id))
        db.commit()
    response = client.delete(f"/api/system/locations/{key}")
    assert response.status_code == 200 and response.json()["default_id"] == "local"
    assert media.read_bytes() == b"keep" and (moved / MARKER).is_file()
    assert client.delete("/api/system/locations/local").status_code == 404


def test_real_external_upload_edit_replace_and_history(client, settings, samples, tmp_path):
    from .conftest import wait_job, wait_ready

    location_id, root = registered(settings, tmp_path)
    data = samples["a"].read_bytes()
    init = client.post(
        "/api/uploads", json={"filename": "clip.mp4", "size": len(data), "storage_id": location_id}
    ).json()
    assert init["storage_id"] == location_id
    assert client.put(f"/api/uploads/{init['id']}?offset=0", content=data).status_code == 200
    result = client.post(f"/api/uploads/{init['id']}/complete")
    assert result.status_code == 200, result.text
    video = wait_ready(client, result.json()["id"])
    assert video["storage_id"] == location_id
    with client.app.state.sessionmaker() as db:
        original = db.get(Video, video["id"]).file_path
    assert abs_path(settings, original).read_bytes() == data
    edited = client.post(
        f"/api/videos/{video['id']}/edit",
        json={"edit": {"op": "rotate"}, "output": {"mode": "replace"}},
    )
    assert edited.status_code == 200, edited.text
    job = wait_job(client, edited.json()["id"])
    assert job["status"] == "succeeded", job["error"]
    final = wait_ready(client, video["id"])
    assert final["storage_id"] == location_id
    assert client.get(f"/api/videos/{video['id']}/download").status_code == 200
    with client.app.state.sessionmaker() as db:
        video_row = db.get(Video, video["id"])
        assert video_row.file_path != original
        backup = db.get(Video, video_row.source_video_id)
        assert backup.deleted_at and backup.file_path == original
        assert abs_path(settings, backup.file_path).read_bytes() == data
    moved = tmp_path / "reconnected-mount"
    root.rename(moved)
    settings.storage_locations[location_id].path = moved
    assert abs_path(settings, original).read_bytes() == data


def test_backup_restores_external_identity_at_new_mount_and_rejects_offline(
    client, settings, tmp_path
):
    from reelvault.backup import BackupError, create_backup, restore_backup
    from reelvault.config import Settings
    from reelvault.db import make_engine, make_sessionmaker

    root = tmp_path / "backup-drive"
    root.mkdir()
    result = client.post("/api/system/locations", json={"name": "archive", "path": str(root)})
    assert result.status_code == 200
    key = next(item["id"] for item in result.json()["items"] if item["id"] != "local")
    original = root / "library" / "original.mp4"
    original.write_bytes(b"preserved-media")
    with client.app.state.sessionmaker() as db:
        video = Video(
            title="backup external", file_path=rel_path(settings, original), status="ready"
        )
        db.add(video)
        db.commit()
        video_id = video.id
    archive = create_backup(settings)
    moved = tmp_path / "restored-mount"
    root.rename(moved)
    entry = settings.storage_locations[key].model_copy(update={"path": moved})
    target = Settings(
        data_dir=tmp_path / "restored-data", update_check=False, storage_locations={key: entry}
    )
    restore_backup(archive, target)
    engine = make_engine(target.db_path)
    try:
        with make_sessionmaker(engine)() as db:
            restored = db.get(Video, video_id)
            assert restored.file_path == f"volumes/{key}/original.mp4"
            assert abs_path(target, restored.file_path).read_bytes() == b"preserved-media"
            from reelvault.models import RuntimeSetting

            value = db.get(RuntimeSetting, "storage_locations").value
            assert value["storage_locations"][key]["path"] == str(moved)
    finally:
        engine.dispose()
    moved.rename(tmp_path / "offline-backup-drive")
    missing_target = Settings(
        data_dir=tmp_path / "missing-target", update_check=False, storage_locations={key: entry}
    )
    with pytest.raises(BackupError, match="缺少"):
        restore_backup(archive, missing_target)
    assert not missing_target.db_path.exists()


def test_shared_filesystem_budget_and_frozen_upload_target(client, settings, tmp_path, monkeypatch):
    from collections import namedtuple

    from reelvault.models import Upload
    from reelvault.storage import MIB, upload_bytes

    location_id, root = registered(settings, tmp_path)
    Usage = namedtuple("Usage", "total used free")
    free = 500 * MIB
    monkeypatch.setattr(
        "reelvault.storage.shutil.disk_usage", lambda _: Usage(1000 * MIB, 1000 * MIB - free, free)
    )
    estimate = client.post(
        "/api/system/storage/estimate",
        json={"upload_sizes": [100 * MIB], "storage_id": location_id},
    ).json()
    assert not estimate[
        "sufficient"
    ]  # 432 MiB staging/derived + 100 MiB destination + 64 MiB floor
    assert estimate["required_bytes"] == upload_bytes(100 * MIB) + 100 * MIB
    assert estimate["locations"][0]["required_bytes"] == estimate["locations"][1]["required_bytes"]
    assert (
        client.post(
            "/api/uploads",
            json={"filename": "full.mp4", "size": 100 * MIB, "storage_id": location_id},
        ).status_code
        == 507
    )
    free = 2000 * MIB
    settings.storage_default = location_id
    response = client.post("/api/uploads", json={"filename": "small.mp4", "size": 10 * MIB})
    assert response.status_code == 200
    settings.storage_default = "local"
    with client.app.state.sessionmaker() as db:
        assert db.get(Upload, response.json()["id"]).storage_id == location_id
    state = client.get("/api/system/storage").json()
    assert state["reserved_bytes"] == upload_bytes(10 * MIB) + 10 * MIB
    root.rename(tmp_path / "disconnected")
    assert (
        client.post(f"/api/uploads/{response.json()['id']}/complete").status_code == 400
    )  # incomplete first
    upload_id = response.json()["id"]
    part = settings.tmp_dir / f"upload-{upload_id}.part"
    part.write_bytes(b"x" * (10 * MIB))
    with client.app.state.sessionmaker() as db:
        record = db.get(Upload, upload_id)
        record.received = record.size
        db.commit()
    assert client.post(f"/api/uploads/{upload_id}/complete").status_code == 503
    assert part.stat().st_size == 10 * MIB
    with client.app.state.sessionmaker() as db:
        assert db.get(Upload, upload_id) is not None
    estimate = client.post(
        "/api/system/storage/estimate", json={"upload_sizes": [1], "storage_id": location_id}
    ).json()
    assert not estimate["sufficient"]
    assert not estimate["locations"][1]["available"]


def test_independent_disks_and_edit_replace_budget(client, settings, tmp_path, monkeypatch):
    import os
    from collections import namedtuple
    from pathlib import Path

    from reelvault.storage import MIB

    location_id, root = registered(settings, tmp_path)
    original_stat = Path.stat

    def fake_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == root / "library":
            values = list(result)
            values[2] += 1
            return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, "stat", fake_stat)
    Usage = namedtuple("Usage", "total used free")
    free = 80 * MIB
    monkeypatch.setattr(
        "reelvault.storage.shutil.disk_usage",
        lambda path: Usage(10_000 * MIB, 0, free if path == root / "library" else 5000 * MIB),
    )
    assert (
        client.post(
            "/api/uploads",
            json={"filename": "big.mp4", "size": 20 * MIB, "storage_id": location_id},
        ).status_code
        == 507
    )
    free = 1000 * MIB
    with client.app.state.sessionmaker() as db:
        video = Video(
            title="external",
            file_path=f"volumes/{location_id}/external.mp4",
            status="ready",
            size=MIB,
            duration=10,
            width=320,
            height=240,
            fps=25,
            bitrate=800_000,
        )
        db.add(video)
        db.commit()
        vid = video.id
    edit = {"op": "rotate", "degrees": 90}
    assert (
        client.post(
            "/api/system/storage/estimate", json={"edit": edit, "output": {"mode": "replace"}}
        ).status_code
        == 404
    )
    estimate = client.post(
        "/api/system/storage/estimate",
        json={
            "video_ids": [vid],
            "edit": edit,
            "output": {"mode": "replace", "storage_id": "local"},
        },
    )
    assert estimate.status_code == 200
    locations = {entry["id"]: entry for entry in estimate.json()["locations"]}
    assert locations[location_id]["required_bytes"] == locations["local"]["required_bytes"] > 0
    # Freeze targets before dispatch; changing defaults cannot redirect queued work.
    monkeypatch.setattr(client.app.state.jobs, "enqueue", lambda _: None)
    job = client.post(
        f"/api/videos/{vid}/edit",
        json={"edit": edit, "output": {"mode": "replace", "storage_id": "local"}},
    )
    assert job.status_code == 200, job.text
    assert job.json()["params"]["output"]["storage_id"] == location_id
    assert job.json()["params"]["storage_plan"] == {
        "local": locations["local"]["required_bytes"],
        location_id: locations[location_id]["required_bytes"],
    }
    root.rename(tmp_path / "missing-disk")
    rejected = client.post(
        f"/api/videos/{vid}/edit", json={"edit": edit, "output": {"mode": "replace"}}
    )
    assert rejected.status_code == 503


def test_import_uses_external_default_and_rejects_managed_root(
    client, settings, tmp_path, monkeypatch
):
    import shutil
    from collections import namedtuple

    from reelvault.storage import MIB

    from .conftest import make_video

    sample_video = make_video(tmp_path / "sample.mp4")
    location_id, root = registered(settings, tmp_path)
    settings.storage_default = location_id
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    settings.import_dir = incoming
    shutil.copy2(sample_video, incoming / "import.mp4")
    result = client.post("/api/system/import")
    assert result.status_code == 200, result.text
    with client.app.state.sessionmaker() as db:
        video = db.scalar(select(Video).where(Video.original_name == "import.mp4"))
        assert video.file_path.startswith(f"volumes/{location_id}/")
        assert abs_path(settings, video.file_path).read_bytes() == sample_video.read_bytes()
    settings.import_dir = root
    assert (
        client.put(
            "/api/system/import-watch", json={"enabled": True, "stable_seconds": 2}
        ).status_code
        == 400
    )
    settings.import_dir = incoming
    shutil.copy2(sample_video, incoming / "no-space.mp4")
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(
        "reelvault.storage.shutil.disk_usage", lambda _: Usage(1000 * MIB, 1000 * MIB, 1)
    )
    assert client.app.state.importer.scan() == 0
    assert list(incoming.glob("no-space.mp4"))
    with client.app.state.sessionmaker() as db:
        assert db.scalar(select(Video).where(Video.original_name == "no-space.mp4")) is None


def test_storage_migration_defaults_old_uploads_and_round_trip(tmp_path):
    from alembic import command
    from sqlalchemy import text

    from reelvault.db import make_engine
    from reelvault.migrate import alembic_config

    engine = make_engine(tmp_path / "legacy.db")
    cfg = alembic_config(str(engine.url))
    command.upgrade(cfg, "0024")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO uploads (id, filename, size, received, tags, created_at) "
                "VALUES ('legacy', 'old.mp4', 12, 4, '[]', CURRENT_TIMESTAMP)"
            )
        )
    command.upgrade(cfg, "head")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT storage_id, received FROM uploads WHERE id='legacy'")
        ).one() == ("local", 4)
    command.downgrade(cfg, "0024")
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT filename FROM uploads WHERE id='legacy'")) == "old.mp4"
        )
    command.upgrade(cfg, "head")
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT storage_id FROM uploads WHERE id='legacy'")) == "local"
        )
    engine.dispose()
