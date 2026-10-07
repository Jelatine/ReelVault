from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event

from alembic import command
from sqlalchemy import event

from reelvault.api import videos as video_api
from reelvault.backup import create_backup, restore_backup, snapshot
from reelvault.config import Settings
from reelvault.library import apply_media_info
from reelvault.media.probe import MediaInfo
from reelvault.metadata import source_metadata
from reelvault.migrate import alembic_config
from reelvault.models import Video

from .conftest import upload_ready, wait_ready
from .test_edit import result_video, run_edit


def tagged_sample(samples, tmp_path):
    path = tmp_path / "metadata.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(samples["a"]),
            "-c",
            "copy",
            "-movflags",
            "use_metadata_tags",
            "-metadata",
            "creation_time=2020-01-01T00:00:00Z",
            "-metadata",
            "com.apple.quicktime.creationdate=2024-06-01T12:30:00+08:00",
            "-metadata",
            "com.apple.quicktime.make=Apple",
            "-metadata",
            "com.apple.quicktime.model=iPhone Test",
            "-metadata",
            "com.apple.quicktime.location.ISO6709=+25.0330+121.5654+15.50/",
            str(path),
        ],
        check=True,
    )
    return path


def test_read_real_tags_edit_partial_clear_reset_and_preserve_original(client, samples, tmp_path):
    path = tagged_sample(samples, tmp_path)
    original = path.read_bytes()
    video = upload_ready(client, path)
    assert video["captured_at"] == "2024-06-01T04:30:00+00:00"
    assert video["metadata"]["device_make"] == "Apple"
    assert video["metadata"]["device_model"] == "iPhone Test"
    assert video["metadata"]["gps"] == {"latitude": 25.033, "longitude": 121.5654, "altitude": 15.5}
    url = f"/api/videos/{video['id']}/metadata"
    response = client.patch(
        url,
        json={
            "captured_at": "2025-01-02T03:00:00+03:00",
            "device_model": " 手动型号 ",
            "gps": {"latitude": 0, "longitude": -180},
            "custom_fields": {" 项目 ": "旅行", "空字段": ""},
        },
    )
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["captured_at"] == "2025-01-02T00:00:00+00:00"
    assert edited["metadata"]["device_make"] == "Apple"
    assert edited["metadata"]["device_model"] == "手动型号"
    assert edited["metadata"]["custom_fields"] == {"项目": "旅行", "空字段": ""}
    assert client.get("/api/videos", params={"captured_after": "2025-01-01"}).json()["total"] == 1
    cleared = client.patch(url, json={"captured_at": None, "gps": None}).json()
    assert cleared["captured_at"] is None and cleared["metadata"]["gps"] is None
    with client.app.state.sessionmaker() as db:
        row = db.get(Video, video["id"])
        apply_media_info(row, MediaInfo(extra={"creation_time": "2030-01-01T00:00:00Z"}), row.size)
        db.commit()
    assert client.get(f"/api/videos/{video['id']}").json()["captured_at"] is None
    reset = client.patch(url, json={"reset": True}).json()
    assert reset["captured_at"] == video["captured_at"]
    assert reset["metadata"]["device_model"] == "iPhone Test"
    assert reset["metadata"]["gps"] == video["metadata"]["gps"]
    assert reset["metadata"]["custom_fields"] == edited["metadata"]["custom_fields"]
    assert reset["metadata"]["overridden"] == []
    assert client.get(video["download_url"]).content == original


def test_metadata_survives_real_save_as_new_replace_and_backup(client, samples, tmp_path):
    video = upload_ready(client, tagged_sample(samples, tmp_path))
    patch = {
        "captured_at": "2022-05-01T00:00:00Z",
        "device_make": "品牌",
        "device_model": "型号",
        "gps": None,
        "custom_fields": {"项目": "编辑素材"},
    }
    source = client.patch(f"/api/videos/{video['id']}/metadata", json=patch).json()
    new = result_video(client, run_edit(client, video["id"], {"op": "rotate", "angle": 90}))
    assert new["metadata"] == source["metadata"] and new["captured_at"] == source["captured_at"]
    assert new["poster_url"] and client.get(new["poster_url"]).status_code == 200
    run_edit(client, video["id"], {"op": "mute"}, {"mode": "replace"})
    replaced = wait_ready(client, video["id"])
    assert (
        replaced["metadata"] == source["metadata"]
        and replaced["captured_at"] == source["captured_at"]
    )
    backup = wait_ready(client, replaced["source_video_id"])
    assert (
        backup["metadata"] == source["metadata"] and backup["captured_at"] == source["captured_at"]
    )


def test_metadata_validation_and_auth(client, samples):
    video = upload_ready(client, samples["a"])
    url = f"/api/videos/{video['id']}/metadata"
    for body in (
        {"gps": {"latitude": 91, "longitude": 0}},
        {"gps": {"latitude": 0, "longitude": "nan"}},
        {"gps": {"latitude": 0}},
        {"captured_at": "bad"},
        {"device_model": "x" * 129},
        {"custom_fields": None},
        {"custom_fields": {" ": "bad"}},
        {"custom_fields": {" a": "one", "a": "two"}},
        {"custom_fields": {"a": "x" * 1001}},
        {"custom_fields": {str(i): "v" for i in range(51)}},
        {"reset": True, "device_model": "bad"},
        {"unknown": "bad"},
    ):
        result = client.patch(url, json=body)
        assert result.status_code == 422, result.text
    assert client.get(f"/api/videos/{video['id']}").json()["metadata"]["overridden"] == []
    client.delete(f"/api/videos/{video['id']}")
    assert client.patch(url, json={"device_make": "x"}).status_code == 404
    client.cookies.clear()
    assert client.patch(url, json={"device_make": "x"}).status_code == 401


def test_concurrent_partial_corrections_wait_before_reading_and_keep_both(
    client, samples, monkeypatch
):
    video = upload_ready(client, samples["a"])
    url = f"/api/videos/{video['id']}/metadata"
    first_read, second_begin, release = Event(), Event(), Event()
    calls = []
    original = video_api.get_video
    engine = client.app.state.sessionmaker.kw["bind"]
    begin_count = []

    def observe_begin(connection, cursor, statement, parameters, context, executemany):
        if statement == "BEGIN IMMEDIATE":
            begin_count.append(1)
            if len(begin_count) == 2:
                second_begin.set()

    def get_video(db, video_id, **kwargs):
        result = original(db, video_id, **kwargs)
        calls.append(1)
        if len(calls) == 1:
            first_read.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(video_api, "get_video", get_video)
    event.listen(engine, "before_cursor_execute", observe_begin)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(client.patch, url, json={"device_make": "品牌"})
            assert first_read.wait(5)
            second = pool.submit(client.patch, url, json={"device_model": "型号"})
            assert second_begin.wait(5)
            assert len(calls) == 1
            release.set()
            assert first.result().status_code == 200
            assert second.result().status_code == 200
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", observe_begin)
    metadata = client.get(f"/api/videos/{video['id']}").json()["metadata"]
    assert metadata["device_make"] == "品牌" and metadata["device_model"] == "型号"


def test_capture_sort_defaults_fallback_and_search_relevance(client, samples):
    a = upload_ready(client, samples["a"])
    b = upload_ready(client, samples["b"])
    client.patch(f"/api/videos/{a['id']}/metadata", json={"captured_at": "2025-01-01T00:00:00Z"})
    client.patch(f"/api/videos/{b['id']}/metadata", json={"captured_at": "2020-01-01T00:00:00Z"})
    with client.app.state.sessionmaker() as db:
        db.get(Video, a["id"]).created_at = datetime(2018, 1, 1, tzinfo=UTC)
        db.get(Video, b["id"]).created_at = datetime(2019, 1, 1, tzinfo=UTC)
        db.commit()
    assert [v["id"] for v in client.get("/api/videos").json()["items"]] == [a["id"], b["id"]]
    client.patch(f"/api/videos/{a['id']}/metadata", json={"captured_at": None})
    assert [v["id"] for v in client.get("/api/videos").json()["items"]] == [b["id"], a["id"]]
    assert [
        v["id"]
        for v in client.get("/api/videos", params={"sort": "captured", "order": "asc"}).json()[
            "items"
        ]
    ] == [a["id"], b["id"]]
    client.patch(f"/api/videos/{a['id']}", json={"title": "旅行 旅行"})
    client.patch(f"/api/videos/{b['id']}", json={"description": "旅行"})
    assert client.get("/api/videos", params={"q": "旅行"}).json()["items"][0]["id"] == a["id"]


def test_offline_backup_restore_preserves_manual_metadata(client, samples, settings, tmp_path):
    video = upload_ready(client, samples["a"])
    client.patch(
        f"/api/videos/{video['id']}/metadata",
        json={
            "captured_at": "2024-02-01T00:00:00Z",
            "device_model": "备份型号",
            "gps": {"latitude": -33.86, "longitude": 151.2},
            "custom_fields": {"项目": "备份项目"},
        },
    )
    archive = create_backup(settings, tmp_path / "metadata.zip")
    destination = Settings(data_dir=tmp_path / "restored")
    destination.ensure_dirs()
    shutil.copytree(settings.library_dir, destination.library_dir, dirs_exist_ok=True)
    shutil.copytree(settings.derived_dir, destination.derived_dir, dirs_exist_ok=True)
    restore_backup(archive, destination)
    with sqlite3.connect(destination.db_path) as db:
        overrides, fields = db.execute(
            "SELECT metadata_overrides, custom_fields FROM videos WHERE id=?", (video["id"],)
        ).fetchone()
        assert json.loads(overrides)["device_model"] == "备份型号"
        assert json.loads(overrides)["gps"]["latitude"] == -33.86
        assert json.loads(fields) == {"项目": "备份项目"}


def test_capture_parsing_and_legacy_upgrade_preserve_video_associations(
    client, samples, settings, tmp_path
):
    assert source_metadata({"creation_time": "bad", "location": "+99.0+121.0/"})["gps"] is None
    assert (
        source_metadata(
            {
                "com.apple.quicktime.creationdate": "bad",
                "CREATION_TIME": "2024-01-01T03:00:00+03:00",
                "MAKE": " Test ",
            }
        )["captured_at"]
        == "2024-01-01T00:00:00+00:00"
    )
    video = upload_ready(client, samples["a"])
    client.patch(f"/api/videos/{video['id']}", json={"tags": ["迁移标签"]})
    copy = tmp_path / "legacy.db"
    snapshot(settings.db_path, copy)
    cfg = alembic_config(f"sqlite:///{copy}")
    command.downgrade(cfg, "0020")
    with sqlite3.connect(copy) as db:
        db.execute(
            "UPDATE videos SET meta=? WHERE id=?",
            (
                json.dumps({"com.apple.quicktime.creationdate": "2024-01-01T03:00:00+03:00"}),
                video["id"],
            ),
        )
        db.commit()
    command.upgrade(cfg, "head")
    with sqlite3.connect(copy) as db:
        row = db.execute(
            "SELECT captured_at, metadata_overrides, custom_fields FROM videos WHERE id=?",
            (video["id"],),
        ).fetchone()
        assert row[0].startswith("2024-01-01 00:00:00")
        assert json.loads(row[1]) == {} and json.loads(row[2]) == {}
        assert db.execute("SELECT count(*) FROM video_tags").fetchone() == (1,)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
