from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reelvault.backup import (
    BackupError,
    backup_before_migration,
    create_backup,
    restore_backup,
    validate_database,
)
from reelvault.config import Settings
from reelvault.db import make_engine
from reelvault.main import create_app
from reelvault.migrate import alembic_config, upgrade
from reelvault.models import Job

from .conftest import login, upload_ready


def test_online_export_offline_restore(
    settings: Settings,
    samples: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.github_token = 'token-${literal}-"quoted"\nline'
    settings.trash_retention_days = 17
    with TestClient(create_app(settings), headers={"X-Requested-With": "ReelVault"}) as client:
        assert client.post("/api/system/backup").status_code == 401
        login(client)
        video = upload_ready(client, samples["a"])
        lut = client.post(
            "/api/lut-assets", files={"file": ("identity.cube", b"LUT_1D_SIZE 2\n0 0 0\n1 1 1\n")}
        ).json()
        subtitle = client.post(
            "/api/subtitle-assets",
            files={
                "file": ("captions.srt", b"1\n00:00:00,000 --> 00:00:03,000\nRestored caption\n")
            },
        ).json()
        assert (
            client.post(
                f"/api/videos/{video['id']}/subtitles",
                json={"asset_id": subtitle["id"], "label": "English", "language": "en"},
            ).status_code
            == 200
        )
        client.patch(f"/api/videos/{video['id']}", json={"rating": 5, "favorite": True})
        collection = client.post(
            "/api/collections", json={"name": "恢复测试", "video_ids": [video["id"]]}
        ).json()
        client.put(f"/api/videos/{video['id']}/playback", json={"position": 1.5, "started": True})
        with client.app.state.sessionmaker() as db:
            paused = Job(
                kind="edit",
                status="paused",
                video_ids=[video["id"]],
                params={"edit": {"op": "rotate", "angle": 90}},
            )
            db.add(paused)
            db.commit()
            paused_id = paused.id
        response = client.post("/api/system/backup")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert not list((settings.data_dir / "backups").glob("*.zip"))
        archive = tmp_path / "export.zip"
        archive.write_bytes(response.content)
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            assert set(z.namelist()) == {"reelvault.db", "config.json", "manifest.json"}
            assert json.loads(z.read("config.json"))["github_token"] == settings.github_token
            assert json.loads(z.read("manifest.json"))["media_included"] is False
        with pytest.raises(BackupError, match="正在使用"):
            restore_backup(archive, settings, replace=True)

    target = Settings(data_dir=tmp_path / "target", update_check=False)
    target.ensure_dirs()
    for name in ("library", "derived", "exports", "assets"):
        shutil.copytree(settings.data_dir / name, target.data_dir / name, dirs_exist_ok=True)
    result = restore_backup(archive, target)
    assert result["safety_backup"] is None
    assert target.db_path.stat().st_mode & 0o777 == 0o600
    monkeypatch.setenv("REELVAULT_CONFIG_FILE", result["config_file"])
    restored = Settings(_env_file=None)
    assert restored.data_dir == target.data_dir
    assert restored.github_token == settings.github_token
    assert restored.trash_retention_days == 17
    monkeypatch.setenv("REELVAULT_TRASH_RETENTION_DAYS", "9")
    assert Settings(_env_file=None).trash_retention_days == 9
    with TestClient(create_app(restored), headers={"X-Requested-With": "ReelVault"}) as client:
        login(client)
        v = client.get(f"/api/videos/{video['id']}").json()
        assert v["rating"] == 5 and v["favorite"] is True
        assert (
            client.get(f"/api/collections/{collection['id']}").json()["items"][0]["id"]
            == video["id"]
        )
        assert client.get(f"/api/videos/{video['id']}/playback").json()["position"] == 1.5
        assert client.get(f"/api/videos/{video['id']}/stream").status_code == 200
        assert client.get(f"/api/videos/{video['id']}/subtitles").json()[0]["label"] == "English"
        (target.assets_dir / f"{subtitle['id']}.webvtt").unlink()
        assert "Restored caption" in client.get(f"/api/subtitle-assets/{subtitle['id']}/vtt").text
        assert client.get("/api/lut-assets").json()[0] == lut
        assert (target.assets_dir / f"{lut['id']}.cube").is_file()
        assert len(client.get("/api/auth/sessions").json()) == 1
        paused = client.get(f"/api/jobs/{paused_id}").json()
        assert paused["status"] == "failed" and "恢复备份" in paused["error"]


def test_corruption_missing_media_and_replace_guard(
    settings: Settings,
    samples: dict[str, Path],
    tmp_path: Path,
) -> None:
    with TestClient(create_app(settings), headers={"X-Requested-With": "ReelVault"}) as client:
        login(client)
        upload_ready(client, samples["a"])
        archive = create_backup(settings, tmp_path / "valid.zip")
    original = settings.db_path.read_bytes()
    with pytest.raises(BackupError, match="--replace"):
        restore_backup(archive, settings)
    corrupt = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(corrupt, "w") as dest:
        for name in source.namelist():
            dest.writestr(name, b"{}" if name == "config.json" else source.read(name))
    with pytest.raises(BackupError, match="校验"):
        restore_backup(corrupt, settings, replace=True)
    assert settings.db_path.read_bytes() == original
    empty_target = Settings(data_dir=tmp_path / "no-media")
    with pytest.raises(BackupError, match="缺少"):
        restore_backup(archive, empty_target)
    assert not empty_target.db_path.exists()
    traversal = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal, "w") as z:
        z.writestr("../escaped", "bad")
    with pytest.raises(BackupError, match="内容"):
        restore_backup(traversal, settings, replace=True)
    assert not (tmp_path / "escaped").exists()


def test_replace_safety_backup_and_rollback(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app(settings), headers={"X-Requested-With": "ReelVault"}) as client:
        login(client)
        archive = create_backup(settings, tmp_path / "valid.zip")
        client.post("/api/collections", json={"name": "旧库现有内容"})
    actual_replace = os.replace

    def fail_config(src: Path, dst: Path) -> None:
        if Path(dst).name == "restored-config.json":
            raise OSError("configuration disk failure")
        actual_replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_config)
    with pytest.raises(OSError, match="disk failure"):
        restore_backup(archive, settings, replace=True)
    with sqlite3.connect(settings.db_path) as db:
        assert db.execute("SELECT name FROM collections").fetchone() == ("旧库现有内容",)
    monkeypatch.setattr(os, "replace", actual_replace)
    result = restore_backup(archive, settings, replace=True)
    assert Path(result["safety_backup"]).is_file()
    with sqlite3.connect(settings.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM collections").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone() == (0,)


def test_backup_before_migration_keeps_old_schema(tmp_path: Path) -> None:
    from alembic import command

    settings = Settings(data_dir=tmp_path / "old")
    settings.ensure_dirs()
    engine = make_engine(settings.db_path)
    command.upgrade(alembic_config(str(engine.url)), "0001")
    engine.dispose()
    archive = backup_before_migration(settings)
    assert archive is not None
    with zipfile.ZipFile(archive) as z:
        assert json.loads(z.read("manifest.json"))["revision"] == "0001"
    engine = make_engine(settings.db_path)
    upgrade(engine)
    engine.dispose()
    assert backup_before_migration(settings) is None
    assert validate_database(settings.db_path) == "0012"
    # CLI import migrates an old backup before installing it.
    target = tmp_path / "restored-old"
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "reelvault.backup",
            "restore",
            "--archive",
            str(archive),
            "--data-dir",
            str(target),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert validate_database(target / "reelvault.db") == "0012"
