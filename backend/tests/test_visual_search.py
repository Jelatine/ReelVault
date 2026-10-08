from __future__ import annotations

import io
import json
import math
import os
import shutil
import threading
import time
import zipfile
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from reelvault.config import Settings
from reelvault.library import abs_path
from reelvault.models import VectorFrame, Video
from reelvault.vector_sql import cosine
from reelvault.vision import MODEL_ID, packed, sample_times, vector

from .conftest import login, upload_ready, wait_job

TOKEN = "test-vision-token-0123456789abcdef0123456789abcdef"


def values(index=0):
    result = [0.0] * 512
    result[index] = 1.0
    return result


@pytest.fixture
def service(settings):
    state = SimpleNamespace(index=0, fail=False, delay=0, calls=0)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.reply({"ready": True})

        def do_POST(self):
            assert self.headers.get("Authorization") == f"Bearer {TOKEN}"
            data = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/embed/text":
                index = 1 if json.loads(data)["texts"][0] == "second" else 0
            else:
                assert data.startswith(b"\xff\xd8")
                state.calls += 1
                index = state.index
                time.sleep(state.delay)
            self.reply({"vectors": [[0.0] * 512 if state.fail else values(index)]})

        def reply(self, extra):
            data = json.dumps(
                {"protocol": 1, "dimension": 512, "model": MODEL_ID, **extra}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings.vision_enabled = True
    settings.vision_url = f"http://127.0.0.1:{server.server_port}"
    settings.vision_token = TOKEN
    yield state
    server.shutdown()
    server.server_close()
    thread.join(5)


def index_video(client, video, **params):
    response = client.post(f"/api/videos/{video['id']}/visual-index", json=params)
    assert response.status_code == 200, response.text
    job = wait_job(client, response.json()["id"])
    assert job["status"] == "succeeded", job["error"]
    return job


def search(client, q="first", **params):
    response = client.get("/api/visual-search", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_visual_search_defaults_and_auth(client, samples):
    video = upload_ready(client, samples["a"])
    assert client.get("/api/visual-search/status").json()["enabled"] is False
    assert (
        client.post(f"/api/videos/{video['id']}/visual-index", json={}).json()["code"]
        == "vision_disabled"
    )
    assert client.get("/api/visual-search?q=sunset").json()["code"] == "vision_disabled"
    client.cookies.clear()
    assert client.get("/api/visual-search/status").status_code == 401


def test_real_ffmpeg_vectors_rank_scope_thumbnail_generation_stale_and_clear(
    client, samples, settings, service
):
    first = upload_ready(client, samples["a"])
    second = upload_ready(client, samples["b"])
    index_video(client, first, interval=1)
    service.index = 1
    index_video(client, second, interval=1)
    hit = search(client, "second")
    assert hit["total"] == 2
    assert hit["items"][0]["video_id"] == second["id"]
    assert hit["items"][0]["score"] == 1
    assert hit["items"][0]["time"] == 0
    assert "?t=0.000000" in hit["items"][0]["url"]
    frame_url = hit["items"][0]["thumbnail"]
    frame = client.get(frame_url)
    assert frame.status_code == 200 and frame.content.startswith(b"\xff\xd8")
    assert frame.headers["content-type"] == "image/jpeg"
    scoped = search(client, "second", video_id=second["id"], page_size=1)
    assert scoped["total"] == 3
    assert (
        search(client, "second", video_id=second["id"], page_size=1, page=2)["items"][0]["time"]
        == 1
    )
    query = client.post("/api/visual-search/image", files={"file": ("query.jpg", frame.content)})
    assert query.status_code == 200, query.text
    assert query.json()["items"][0]["video_id"] == second["id"]
    assert not list(settings.tmp_dir.glob("vision-query-*"))
    # Private worker credentials are not copied into published frame directories.
    assert all(p.suffix == ".jpg" for p in settings.derived_dir.glob("*/vision/*/*"))
    assert TOKEN not in json.dumps(client.get("/api/jobs").json())
    assert client.get("/api/visual-search?q=first").headers["cache-control"] == "no-store"
    old_url = frame_url
    index_video(client, second, interval=1)
    assert client.get(old_url).status_code == 404
    with client.app.state.sessionmaker() as db:
        original = abs_path(settings, db.get(Video, first["id"]).file_path)
    os.utime(original, ns=(original.stat().st_atime_ns, original.stat().st_mtime_ns + 1000000))
    assert client.get(f"/api/videos/{first['id']}/visual-index").json()["stale"] is True
    assert search(client)["total"] == 1
    assert client.delete(f"/api/videos/{second['id']}/visual-index").status_code == 200
    assert search(client)["total"] == 0
    with client.app.state.sessionmaker() as db:
        assert not list(db.scalars(select(VectorFrame).where(VectorFrame.video_id == second["id"])))


def test_failed_rebuild_preserves_index_retry_replaces_and_rejects_query_images(
    client, samples, service
):
    video = upload_ready(client, samples["a"])
    index_video(client, video, interval=1)
    original = search(client)["items"][0]["thumbnail"]
    service.fail = True
    bad = client.post(f"/api/videos/{video['id']}/visual-index", json={}).json()
    assert wait_job(client, bad["id"])["status"] == "failed"
    service.fail = False
    assert search(client)["items"][0]["thumbnail"] == original
    retry = client.post(f"/api/jobs/{bad['id']}/retry")
    assert retry.status_code == 200, retry.text
    assert wait_job(client, retry.json()["id"])["status"] == "succeeded"
    assert search(client)["items"][0]["thumbnail"] != original
    for name, data in (("bad.gif", b"bad"), ("bad.png", b"bad"), ("empty.jpg", b"")):
        assert (
            client.post("/api/visual-search/image", files={"file": (name, data)}).status_code == 400
        )
    assert client.get("/api/visual-search", params={"q": "x" * 513}).status_code == 422


def test_control_real_worker_pause_cancel_dedup_and_other_video_parallel(
    client, samples, settings, service
):
    video = upload_ready(client, samples["long"])
    other = upload_ready(client, samples["b"])
    service.delay = 0.2
    url = f"/api/videos/{video['id']}/visual-index"
    job = client.post(url, json={"interval": 1}).json()
    assert client.post(url, json={"interval": 1}).json()["id"] == job["id"]
    deadline = time.monotonic() + 10
    while service.calls < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert service.calls >= 2
    assert client.post(f"/api/jobs/{job['id']}/pause").json()["status"] == "paused"
    time.sleep(0.3)
    frozen = service.calls
    time.sleep(0.3)
    assert service.calls == frozen
    assert client.get("/healthz").status_code == 200
    other_job = client.post(
        f"/api/videos/{other['id']}/edit",
        json={"edit": {"op": "compress", "codec": "h264", "crf": 30}},
    ).json()
    assert wait_job(client, other_job["id"])["status"] == "succeeded"
    assert client.delete(url).json()["code"] == "vision_index_busy"
    assert client.post(f"/api/jobs/{job['id']}/resume").status_code == 200
    deadline = time.monotonic() + 5
    while service.calls == frozen and time.monotonic() < deadline:
        time.sleep(0.02)
    assert service.calls > frozen
    client.post(f"/api/jobs/{job['id']}/cancel")
    assert wait_job(client, job["id"])["status"] == "canceled"
    assert not list(settings.tmp_dir.glob(f"job-{job['id']}"))
    assert client.get(url).json()["index"] is None


def test_sampling_vectors_configuration_and_native_distance():
    assert sample_times(4, 1, 240) == [0, 1, 2, 3]
    assert sample_times(600, 1, 3) == [0, 200, 400]
    assert sample_times(4, 1, 240, 1) == [1, 2, 3]
    for duration in (math.nan, math.inf, 0, -1, 24 * 3600 + 1):
        with pytest.raises(ValueError):
            sample_times(duration, 1, 240)
    for result in ([0] * 512, [1] * 511, [math.nan] * 512, [True] + [0] * 511):
        with pytest.raises(ValueError):
            vector(result)
    assert cosine(packed(values()), packed(values())) == pytest.approx(0)
    assert cosine(packed(values()), packed(values(1))) == pytest.approx(1)
    assert TOKEN not in str(Settings(vision_token=TOKEN))
    assert "vision_token" not in Settings(vision_token=TOKEN).model_dump()
    with pytest.raises(ValidationError):
        Settings(vision_enabled=True, vision_token="")
    for url in ("file:///tmp/models", "http://u:p@localhost", "http://localhost?token=bad"):
        with pytest.raises(ValidationError):
            Settings(vision_url=url)


def test_visual_metadata_backup_restore_and_migration_roundtrip(
    settings, samples, service, tmp_path, monkeypatch
):
    from alembic import command
    from fastapi.testclient import TestClient

    from reelvault.backup import restore_backup
    from reelvault.db import make_engine
    from reelvault.main import create_app
    from reelvault.migrate import alembic_config, upgrade

    with TestClient(create_app(settings), headers={"X-Requested-With": "ReelVault"}) as client:
        login(client)
        video = upload_ready(client, samples["a"])
        index_video(client, video, interval=1)
        expected = search(client)["items"][0]
        response = client.post("/api/system/backup")
        assert response.status_code == 200
        archive = tmp_path / "backup.zip"
        archive.write_bytes(response.content)
        with zipfile.ZipFile(io.BytesIO(response.content)) as zipped:
            config = json.loads(zipped.read("config.json"))
            assert (
                "vision_token" not in config
                and "vision_url" not in config
                and "vision_enabled" not in config
            )
    target = Settings(data_dir=tmp_path / "restored", update_check=False)
    target.ensure_dirs()
    for name in ("library", "derived"):
        shutil.copytree(settings.data_dir / name, target.data_dir / name, dirs_exist_ok=True)
    restored = restore_backup(archive, target)
    monkeypatch.setenv("REELVAULT_CONFIG_FILE", restored["config_file"])
    config = Settings(_env_file=None)
    assert config.vision_enabled is False
    config.vision_enabled, config.vision_url, config.vision_token = True, settings.vision_url, TOKEN
    with TestClient(create_app(config), headers={"X-Requested-With": "ReelVault"}) as client:
        login(client)
        actual = search(client)["items"][0]
        assert actual["video_id"] == expected["video_id"] and actual["score"] == expected["score"]
        assert client.get(actual["thumbnail"]).status_code == 200
    command.downgrade(alembic_config(f"sqlite:///{config.db_path}"), "0029")
    engine = make_engine(config.db_path)
    upgrade(engine)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("select count(*) from video_vector_indexes").scalar() == 0
        assert connection.exec_driver_sql("select count(*) from videos").scalar() == 1
    engine.dispose()


@pytest.mark.skipif(
    not os.environ.get("REELVAULT_TEST_VISION_URL"), reason="Live prepared CLIP service required"
)
def test_actual_clip_video_index_and_text_image_query(client, samples, settings):
    settings.vision_enabled = True
    settings.vision_url = os.environ["REELVAULT_TEST_VISION_URL"]
    settings.vision_token = os.environ["REELVAULT_TEST_VISION_TOKEN"]
    video = upload_ready(client, samples["a"])
    index_video(client, video, interval=1)
    result = search(client, "彩色的测试图案")
    assert result["total"] == 1 and result["items"][0]["video_id"] == video["id"]
    frame = client.get(result["items"][0]["thumbnail"])
    result = client.post("/api/visual-search/image", files={"file": ("query.jpg", frame.content)})
    assert result.status_code == 200
    assert result.json()["items"][0]["video_id"] == video["id"]
    assert result.json()["items"][0]["score"] > 0.999
