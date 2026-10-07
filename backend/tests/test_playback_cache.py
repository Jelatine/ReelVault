from __future__ import annotations

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor

from reelvault.library import abs_path
from reelvault.media.probe import probe
from reelvault.models import Video

from .conftest import make_video, upload_ready, wait_job
from .test_shares import create, guest


def test_real_hevc_deferred_copy_range_download_sharing_clear_and_regeneration(
    client, settings, tmp_path
):
    settings.playable_eager_max_mb = 0
    source = make_video(
        tmp_path / "hevc.mp4",
        duration=1,
        codec="libx265",
        extra=["-preset", "ultrafast", "-x265-params", "log-level=error:pools=1"],
    )
    video = upload_ready(client, source)
    vid = video["id"]
    url = f"/api/videos/{vid}/playback-cache"
    folder = settings.derived_dir / vid
    assert not list(folder.glob("playable*.mp4"))
    assert client.get(url).headers["cache-control"] == "no-store"
    assert client.get(url).json()["ready"] is False
    assert client.get(video["stream_url"]).json()["code"] == "playback_cache_required"
    assert client.get(video["download_url"]).content == source.read_bytes()
    assert (
        client.post("/api/shares", json={"video_id": vid}).json()["code"]
        == "playback_cache_required"
    )
    job = client.post(url).json()
    assert wait_job(client, job["id"])["status"] == "succeeded"
    data = client.get(url).json()
    assert data["ready"] and data["cached"] and data["size"] > 0
    copy = folder / f"playable-{job['id']}.mp4"
    info = asyncio.run(probe("ffprobe", str(copy)))
    assert info.video_codec == "h264" and info.audio_codec == "aac"
    assert abs(info.duration - 1) < 0.1
    assert client.post(url).json() == {"cached": True}
    response = client.get(video["stream_url"], headers={"Range": "bytes=0-99"})
    assert response.status_code == 206 and response.content == copy.read_bytes()[:100]
    _, prefix = create(client, vid, allow_download=True)
    visitor = guest(client)
    assert visitor.get(prefix).json()["items"][0]["playback_ready"]
    stream = f"{prefix}/videos/{vid}/stream"
    assert visitor.get(stream).content == copy.read_bytes()
    assert client.delete(url).json() == {"cleared": True}
    assert not copy.exists()
    assert visitor.get(stream).status_code == 409
    assert visitor.get(prefix).json()["items"][0]["playback_ready"] is False
    assert visitor.post(url).status_code == 401
    assert visitor.get(f"{prefix}/videos/{vid}/download").content == source.read_bytes()
    rebuilt = client.post(url).json()
    assert wait_job(client, rebuilt["id"])["status"] == "succeeded"
    assert visitor.get(stream).status_code == 200
    with client.app.state.sessionmaker() as db:
        original = abs_path(settings, db.get(Video, vid).file_path)
    stat = original.stat()
    os.utime(original, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert client.get(url).json()["stale"]
    assert client.get(video["stream_url"]).status_code == 409
    assert visitor.get(stream).status_code == 409
    assert client.delete(url).status_code == 200
    assert original.read_bytes() == source.read_bytes()
    client.cookies.clear()
    for method in (client.get, client.post, client.delete):
        assert method(url).status_code == 401


def test_small_eager_remux_cache_missing_and_reprocess_preserves_original(
    client, settings, samples
):
    video = upload_ready(client, samples["mkv"])
    url = f"/api/videos/{video['id']}/playback-cache"
    assert client.get(url).json()["cached"]
    with client.app.state.sessionmaker() as db:
        row = db.get(Video, video["id"])
        copy = abs_path(settings, row.playable_path)
        original = abs_path(settings, row.file_path)
    assert asyncio.run(probe("ffprobe", str(copy))).video_codec == "h264"
    copy.unlink()
    assert client.get(url).json()["stale"]
    assert client.get(video["stream_url"]).status_code == 409
    job = client.post(url).json()
    assert wait_job(client, job["id"])["status"] == "succeeded"
    settings.playable_eager_max_mb = 0
    job_id = client.post(f"/api/videos/{video['id']}/reprocess").json()["job_id"]
    assert wait_job(client, job_id)["status"] == "succeeded"
    assert not client.get(url).json()["cached"]
    assert original.read_bytes() == samples["mkv"].read_bytes()
    assert not list((settings.derived_dir / video["id"]).glob("playable*.mp4"))


def test_concurrent_dedup_pause_failure_retry_cancel_and_temp_cleanup(
    client, settings, samples, monkeypatch
):
    from reelvault.media import derive
    from reelvault.storage import MIB

    settings.playable_eager_max_mb = 0
    video = upload_ready(client, samples["mkv"])
    url = f"/api/videos/{video['id']}/playback-cache"
    manager = client.app.state.jobs
    enqueue = manager.enqueue
    monkeypatch.setattr(manager, "enqueue", lambda _: None)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: client.post(url), range(6)))
    assert all(r.status_code == 200 for r in results)
    assert len({r.json()["id"] for r in results}) == 1
    job = results[0].json()
    assert job["params"]["storage_plan"]["local"] >= 32 * MIB
    assert client.post(f"/api/jobs/{job['id']}/pause").status_code == 200
    assert client.post(url).json()["id"] == job["id"]
    assert client.delete(url).json()["code"] == "playback_cache_busy"
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
    monkeypatch.setattr(manager, "enqueue", enqueue)
    original = derive.make_playable

    async def fail(*args, **kwargs):
        args[3].write_bytes(b"partial")
        raise RuntimeError("test encoder failure")

    monkeypatch.setattr(derive, "make_playable", fail)
    failed = client.post(url).json()
    assert wait_job(client, failed["id"])["status"] == "failed"
    assert not (settings.tmp_dir / f"job-{failed['id']}").exists()
    assert client.get(f"/api/videos/{video['id']}").json()["status"] == "ready"
    monkeypatch.setattr(derive, "make_playable", original)
    retry = client.post(f"/api/jobs/{failed['id']}/retry").json()
    assert wait_job(client, retry["id"])["status"] == "succeeded"
    assert client.post(f"/api/jobs/{failed['id']}/retry").status_code == 409
    assert client.delete(url).status_code == 200
    started = asyncio.Event()

    async def slow(*args, **kwargs):
        args[3].write_bytes(b"partial")
        started.set()
        while not args[4].canceled:
            await asyncio.sleep(0.01)

    monkeypatch.setattr(derive, "make_playable", slow)
    canceled = client.post(url).json()
    deadline = time.monotonic() + 5
    while not started.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert started.is_set()
    client.post(f"/api/jobs/{canceled['id']}/cancel")
    assert wait_job(client, canceled["id"])["status"] == "canceled"
    assert not (settings.tmp_dir / f"job-{canceled['id']}").exists()
    assert not client.get(url).json()["ready"]
    with client.app.state.sessionmaker() as db:
        assert db.get(Video, video["id"]).playable_path is None


def test_budget_rejects_without_jobs_and_source_change_never_publishes(
    client, settings, samples, monkeypatch
):
    import shutil

    from sqlalchemy import select

    from reelvault.media import derive
    from reelvault.models import Job

    settings.playable_eager_max_mb = 0
    video = upload_ready(client, samples["mkv"])
    url = f"/api/videos/{video['id']}/playback-cache"
    usage = shutil.disk_usage
    with monkeypatch.context() as context:
        context.setattr(
            shutil, "disk_usage", lambda _: usage(settings.data_dir)._replace(free=1024)
        )
        assert client.post(url).status_code == 507
    with client.app.state.sessionmaker() as db:
        assert list(db.scalars(select(Job).where(Job.kind == "playable"))) == []
        original = abs_path(settings, db.get(Video, video["id"]).file_path)
    convert = derive.make_playable

    async def changed(*args, **kwargs):
        result = await convert(*args, **kwargs)
        stat = original.stat()
        os.utime(original, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        return result

    monkeypatch.setattr(derive, "make_playable", changed)
    job = client.post(url).json()
    assert wait_job(client, job["id"])["status"] == "failed"
    assert not list((settings.derived_dir / video["id"]).glob("playable*.mp4"))
    assert not (settings.tmp_dir / f"job-{job['id']}").exists()
    assert original.read_bytes() == samples["mkv"].read_bytes()
    assert not client.get(url).json()["ready"]
    monkeypatch.setattr(derive, "make_playable", convert)
    retried = client.post(f"/api/jobs/{job['id']}/retry").json()
    assert wait_job(client, retried["id"])["status"] == "succeeded"
    assert client.get(url).json()["ready"]


def test_actual_default_size_boundary_with_sparse_hevc(client, settings):
    from reelvault.storage import MIB

    source = make_video(
        settings.library_dir / "large.mp4",
        duration=0.4,
        audio=False,
        codec="libx265",
        extra=["-preset", "ultrafast", "-x265-params", "log-level=error:pools=1"],
    )
    with source.open("r+b") as output:
        output.truncate(256 * MIB + 1)
    with client.app.state.sessionmaker() as db:
        row = Video(title="large", file_path="library/large.mp4", size=source.stat().st_size)
        db.add(row)
        db.commit()
        vid = row.id
        job = client.app.state.jobs.submit(db, "ingest", {}, [vid])
        job_id = job.id
    finished = wait_job(client, job_id)
    assert finished["status"] == "succeeded", finished
    assert finished["params"]["storage_bytes"] == 32 * MIB
    url = f"/api/videos/{vid}/playback-cache"
    assert not client.get(url).json()["cached"]
    with source.open("r+b") as output:
        output.truncate(256 * MIB)
    job_id = client.post(f"/api/videos/{vid}/reprocess").json()["job_id"]
    assert wait_job(client, job_id)["status"] == "succeeded"
    assert client.get(url).json()["cached"]
    assert source.stat().st_size == 256 * MIB


def test_cache_survives_backup_restore_restart_and_orphan_cleanup(
    client, settings, samples, tmp_path
):
    import shutil

    from fastapi.testclient import TestClient

    from reelvault.backup import create_backup, restore_backup
    from reelvault.main import create_app

    from .conftest import HEADERS, login

    settings.playable_eager_max_mb = 0
    video = upload_ready(client, samples["mkv"])
    url = f"/api/videos/{video['id']}/playback-cache"
    job = client.post(url).json()
    assert wait_job(client, job["id"])["status"] == "succeeded"
    archive = create_backup(settings, tmp_path / "cache-backup.zip")
    target = settings.model_copy(update={"data_dir": tmp_path / "restored"})
    target.ensure_dirs()
    shutil.copytree(settings.library_dir, target.library_dir, dirs_exist_ok=True)
    shutil.copytree(settings.derived_dir, target.derived_dir, dirs_exist_ok=True)
    orphan = target.derived_dir / video["id"] / "playable-orphan.mp4"
    orphan.write_bytes(b"uncommitted cache")
    restore_backup(archive, target)
    with TestClient(create_app(target), headers=HEADERS) as restored:
        login(restored)
        assert restored.get(url).json()["cached"]
        assert restored.get(video["stream_url"]).status_code == 200
        assert not orphan.exists()
        with restored.app.state.sessionmaker() as db:
            # Pre-upgrade eager caches without signatures remain usable.
            row = db.get(Video, video["id"])
            row.meta = {k: v for k, v in row.meta.items() if k != "playable_signature"}
            db.commit()
        assert restored.get(video["stream_url"]).status_code == 200
