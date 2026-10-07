import contextlib
import functools
import http.server
import importlib.util
import shutil
import threading
import time
from types import SimpleNamespace

import pytest

from reelvault.models import Job
from reelvault.storage import MIB

from .conftest import wait_job, wait_ready


@contextlib.contextmanager
def video_source(directory, *, slow=False):
    stopped = threading.Event()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def copyfile(self, source, output):
            if not slow:
                return super().copyfile(source, output)
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                while not stopped.is_set() and (chunk := source.read(4096)):
                    output.write(chunk)
                    output.flush()
                    time.sleep(0.02)

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(directory))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_disabled_unavailable_rights_and_private_url_gates(client, settings, monkeypatch):
    body = {"url": "http://127.0.0.1/video.mp4", "acknowledge_rights": True}
    assert client.get("/api/system/link-import").json()["enabled"] is False
    assert client.post("/api/import-links", json=body).json()["code"] == "link_import_disabled"
    monkeypatch.setattr("reelvault.api.links.downloader_command", lambda _: None)
    assert (
        client.put("/api/system/link-import", json={"link_import_enabled": True}).status_code == 409
    )
    monkeypatch.setattr("reelvault.api.links.downloader_command", lambda _: ["yt-dlp"])
    assert (
        client.put(
            "/api/system/link-import",
            json={
                "link_import_enabled": True,
                "link_import_max_mb": 16,
            },
        ).status_code
        == 200
    )
    assert (
        client.post("/api/import-links", json={"url": body["url"]}).json()["code"]
        == "link_import_rights_required"
    )
    blocked = client.post("/api/import-links", json=body)
    assert blocked.status_code == 400 and blocked.json()["code"] == "link_import_url_invalid"
    assert (
        client.post("/api/import-links", json={**body, "url": "file:///etc/passwd"}).status_code
        == 400
    )
    settings.link_import_allow_private = True
    monkeypatch.setattr(
        "reelvault.storage.shutil.disk_usage",
        lambda _: SimpleNamespace(
            total=10**10,
            used=10**10 - 64 * MIB,
            free=64 * MIB,
        ),
    )
    assert client.post("/api/import-links", json=body).status_code == 507
    with client.app.state.sessionmaker() as db:
        assert not db.query(Job).filter(Job.kind == "link_import").count()


def test_link_preferences_survive_restart_and_metadata_restore(settings, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from reelvault.backup import create_backup, restore_backup
    from reelvault.config import Settings
    from reelvault.main import create_app

    from .conftest import HEADERS, login

    monkeypatch.setattr("reelvault.api.links.downloader_command", lambda _: ["yt-dlp"])
    with TestClient(create_app(settings), headers=HEADERS) as first:
        login(first)
        assert (
            first.put(
                "/api/system/link-import",
                json={
                    "link_import_enabled": True,
                    "link_import_max_mb": 32,
                    "link_import_timeout_minutes": 17,
                },
            ).status_code
            == 200
        )
        archive = create_backup(settings, tmp_path / "links.zip")
    with TestClient(
        create_app(Settings(data_dir=settings.data_dir, update_check=False)), headers=HEADERS
    ) as second:
        login(second)
        state = second.get("/api/system/link-import").json()
        assert state["enabled"] and state["max_mb"] == 32 and state["timeout_minutes"] == 17
    target = Settings(data_dir=tmp_path / "restored", update_check=False)
    restore_backup(archive, target)
    with TestClient(create_app(target), headers=HEADERS) as restored:
        login(restored)
        assert restored.get("/api/system/link-import").json() == state


@pytest.mark.skipif(
    importlib.util.find_spec("yt_dlp") is None, reason="Optional yt-dlp extra not installed"
)
def test_real_download_is_imported_and_processed(client, settings, samples, tmp_path):
    settings.link_import_enabled = True
    settings.link_import_allow_private = True
    settings.link_import_max_mb = 16
    source = tmp_path / "source"
    source.mkdir()
    shutil.copy2(samples["a"], source / "clip.mp4")
    folder = client.post("/api/folders", json={"name": "Imported"}).json()
    with video_source(source) as origin:
        response = client.post(
            "/api/import-links",
            json={
                "url": origin + "/clip.mp4",
                "acknowledge_rights": True,
                "title": "Custom imported title",
                "folder_id": folder["id"],
            },
        )
        assert response.status_code == 200, response.text
        job = wait_job(client, response.json()["id"])
        assert job["status"] == "succeeded", job
        video_id = job["result_video_id"]
        video = wait_ready(client, video_id)
        assert video["status"] == "ready", video
        assert video["title"] == "Custom imported title" and video["folder_id"] == folder["id"]
        assert client.get(f"/api/videos/{video_id}/download").content == samples["a"].read_bytes()
        assert client.get(f"/api/videos/{video_id}/poster.jpg").status_code == 200
    assert not list(settings.tmp_dir.glob("link-*"))


@pytest.mark.skipif(
    importlib.util.find_spec("yt_dlp") is None, reason="Optional yt-dlp extra not installed"
)
def test_real_download_pause_resume_cancel_cleans_work(client, settings, samples, tmp_path):
    settings.link_import_enabled = True
    settings.link_import_allow_private = True
    settings.link_import_max_mb = 16
    source = tmp_path / "slow"
    source.mkdir()
    content = samples["a"].read_bytes()
    (source / "slow.mp4").write_bytes(content * (4 * MIB // len(content) + 1))
    with video_source(source, slow=True) as origin:
        response = client.post(
            "/api/import-links",
            json={
                "url": origin + "/slow.mp4",
                "acknowledge_rights": True,
            },
        )
        job_id = response.json()["id"]
        for _ in range(100):
            ctx = client.app.state.jobs.running.get(job_id)
            if (
                ctx
                and ctx.handle.process
                and client.get(f"/api/jobs/{job_id}").json()["progress"] > 0
            ):
                break
            time.sleep(0.05)
        assert ctx and ctx.handle.process
        process = ctx.handle.process
        assert ctx.handle.process_group
        assert client.post(f"/api/jobs/{job_id}/pause").status_code == 200
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "paused"
        time.sleep(0.1)
        assert client.post(f"/api/jobs/{job_id}/resume").status_code == 200
        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
        job = wait_job(client, job_id)
        assert job["status"] == "canceled" and not job["result_video_id"]
        assert process.returncode is not None
    assert not list(settings.tmp_dir.glob("link-*"))


@pytest.mark.skipif(
    importlib.util.find_spec("yt_dlp") is None, reason="Optional yt-dlp extra not installed"
)
def test_download_failure_retry_and_external_storage_choice_are_preserved(
    client, settings, samples, tmp_path
):
    from .test_locations import registered

    settings.link_import_enabled = True
    settings.link_import_allow_private = True
    settings.link_import_max_mb = 16
    location_id, root = registered(settings, tmp_path)
    source = tmp_path / "retry"
    source.mkdir()
    with video_source(source) as origin:
        response = client.post(
            "/api/import-links",
            json={
                "url": origin + "/later.mp4",
                "acknowledge_rights": True,
                "storage_id": location_id,
                "title": "Retry title",
            },
        )
        failed = wait_job(client, response.json()["id"])
        assert failed["status"] == "failed" and not failed["result_video_id"]
        assert not list(settings.tmp_dir.glob("link-*"))
        shutil.copy2(samples["a"], source / "later.mp4")
        settings.storage_default = "local"
        retried = client.post(f"/api/jobs/{failed['id']}/retry")
        assert retried.status_code == 200, retried.text
        complete = wait_job(client, retried.json()["id"])
        assert complete["status"] == "succeeded", complete
        video = wait_ready(client, complete["result_video_id"])
        assert video["title"] == "Retry title" and video["storage_id"] == location_id
        assert list((root / "library").glob("*.mp4"))


def test_downloader_enables_only_local_deno_with_packaged_solver(tmp_path, monkeypatch):
    from reelvault.link_download import arguments

    monkeypatch.setattr(
        "reelvault.link_download.shutil.which",
        lambda name: "/trusted/bin/deno" if name == "deno" else "/trusted/bin/ffmpeg",
    )
    args = arguments(
        ["python", "-m", "yt_dlp"],
        "https://example.com/video",
        tmp_path,
        "http://proxy",
        16 * MIB,
        "ffmpeg",
    )
    assert args[args.index("--js-runtimes") + 1] == "deno:/trusted/bin/deno"
    assert "--no-js-runtimes" in args and "--no-remote-components" in args
    assert "--no-plugin-dirs" in args and args[-2:] == ["--", "https://example.com/video"]
    monkeypatch.setattr("reelvault.link_download.shutil.which", lambda _: None)
    args = arguments(
        ["yt-dlp"], "https://example.com/video", tmp_path, "http://proxy", 16 * MIB, "ffmpeg"
    )
    assert "--js-runtimes" not in args
