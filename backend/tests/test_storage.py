from __future__ import annotations

from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import select

from reelvault.main import create_app
from reelvault.models import Job, RuntimeSetting, Upload, Video
from reelvault.storage import MIB, snapshot, upload_bytes

from .conftest import HEADERS, login, upload, wait_ready

Usage = namedtuple("Usage", "total used free")


def disk(monkeypatch, free, total=10 * 1024**3):
    monkeypatch.setattr(
        "reelvault.storage.shutil.disk_usage", lambda _: Usage(total, total - free, free)
    )


def source(client, title="test"):
    with client.app.state.sessionmaker() as db:
        video = Video(
            title=title,
            file_path=f"library/{title}.mp4",
            status="ready",
            size=MIB,
            duration=10,
            width=1920,
            height=1080,
            fps=30,
            bitrate=800_000,
        )
        db.add(video)
        db.commit()
        return video.id


def test_authenticated_estimates_and_persistent_thresholds(settings):
    with TestClient(create_app(settings), headers=HEADERS) as client:
        assert client.get("/api/system/storage").status_code == 401
        login(client)
        assert (
            client.put(
                "/api/system/storage", json={"warning_mb": -1, "warning_percent": 1}
            ).status_code
            == 422
        )
        result = client.put("/api/system/storage", json={"warning_mb": 42, "warning_percent": 3})
        assert result.status_code == 200
        assert result.json()["warning_mb"] == 42
    settings.storage_warning_mb = 1024
    settings.storage_warning_percent = 5
    with TestClient(create_app(settings), headers=HEADERS) as client:
        login(client)
        state = client.get("/api/system/storage").json()
        assert (state["warning_mb"], state["warning_percent"]) == (42, 3)
        with client.app.state.sessionmaker() as db:
            assert db.get(RuntimeSetting, "storage").value["storage_warning_mb"] == 42


def test_warning_includes_uploads_and_uses_larger_threshold(client, settings, monkeypatch):
    disk(monkeypatch, 1500 * MIB, 10_000 * MIB)
    result = client.post("/api/uploads", json={"filename": "clip.mp4", "size": 200 * MIB})
    assert result.status_code == 200
    state = client.get("/api/system/storage").json()
    assert state["reserved_bytes"] == upload_bytes(200 * MIB)
    assert state["low_space"] and state["warning_bytes"] == 1024 * MIB
    # Actual partial bytes are already included in free space, not reserved twice.
    (settings.tmp_dir / f"upload-{result.json()['id']}.part").write_bytes(b"x" * MIB)
    after = client.get("/api/system/storage").json()
    assert after["reserved_bytes"] == state["reserved_bytes"] - MIB
    client.delete(f"/api/uploads/{result.json()['id']}")
    assert client.get("/api/system/storage").json()["reserved_bytes"] == 0
    client.put("/api/system/storage", json={"warning_mb": 0, "warning_percent": 20})
    assert client.get("/api/system/storage").json()["warning_bytes"] == 2000 * MIB
    client.put("/api/system/storage", json={"warning_mb": 0, "warning_percent": 0})
    assert not client.get("/api/system/storage").json()["low_space"]


def test_concurrent_uploads_cannot_overbook(client, monkeypatch):
    disk(monkeypatch, 500 * MIB)

    def submit(_):
        return client.post(
            "/api/uploads", json={"filename": "clip.mp4", "size": 100 * MIB}
        ).status_code

    with ThreadPoolExecutor(max_workers=3) as pool:
        assert sorted(pool.map(submit, range(3))) == [200, 507, 507]
    with client.app.state.sessionmaker() as db:
        assert len(list(db.scalars(select(Upload)))) == 1


def test_edit_estimates_cover_duration_resolution_and_retained_originals(client):
    vid = source(client)

    def estimate(edit, **extra):
        response = client.post(
            "/api/system/storage/estimate", json={"video_ids": [vid], "edit": edit, **extra}
        )
        assert response.status_code == 200, response.text
        return response.json()["required_bytes"]

    rotate = estimate({"op": "rotate"})
    slow = estimate({"op": "speed", "factor": 0.25})
    assert slow > rotate * 2
    assert estimate({"op": "crop", "x": 0, "y": 0, "width": 320, "height": 240}) < rotate
    assert estimate({"op": "animation", "start": 0, "end": 10, "width": 1920, "fps": 60}) > slow
    assert estimate({"op": "compress", "target_size_mb": 100}) > 300 * MIB
    assert estimate({"op": "compress", "target_size_mb": 1, "codec": "h265"}) > estimate(
        {"op": "compress", "target_size_mb": 1, "codec": "h264"}
    )
    assert estimate({"op": "effect", "mode": "reverse", "start": 0, "end": 5}) > 1024 * MIB
    second = source(client, "second")
    batch = client.post(
        "/api/system/storage/estimate",
        json={"video_ids": [vid, second], "edit": {"op": "rotate"}, "batch": True},
    ).json()
    assert batch["required_bytes"] == rotate * 2
    assert (
        client.post(
            "/api/system/storage/estimate",
            json={"video_ids": ["missing"], "edit": {"op": "rotate"}},
        ).status_code
        == 404
    )
    assert (
        client.post("/api/system/storage/estimate", json={"upload_sizes": [-1]}).status_code == 400
    )


def test_edit_batch_retry_and_paused_jobs_share_budget(client, monkeypatch):
    first, second = source(client), source(client, "second")
    # Hold workers by pausing new work before waking the event loop.
    manager = client.app.state.jobs

    def hold(job_id):
        with manager.sessionmaker() as db:
            job = db.get(Job, job_id)
            if job.status == "queued":
                job.status = "paused"
                db.commit()

    monkeypatch.setattr(manager, "enqueue", hold)
    needed = client.post(
        "/api/system/storage/estimate", json={"video_ids": [first], "edit": {"op": "rotate"}}
    ).json()["required_bytes"]
    disk(monkeypatch, needed + 65 * MIB)
    assert (
        client.post(
            "/api/jobs/batch", json={"video_ids": [first, second], "edit": {"op": "rotate"}}
        ).status_code
        == 507
    )
    with manager.sessionmaker() as db:
        assert not list(db.scalars(select(Job)))
    job = client.post(
        f"/api/videos/{first}/edit", json={"edit": {"op": "rotate"}, "output": {"mode": "replace"}}
    ).json()
    assert job["params"]["storage_bytes"] == needed
    assert client.post("/api/uploads", json={"filename": "tiny.mp4", "size": 1}).status_code == 507
    with manager.sessionmaker() as db:
        saved = db.get(Job, job["id"])
        saved.status = "failed"
        db.commit()
    disk(monkeypatch, 1 * MIB)
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 507
    disk(monkeypatch, needed + 65 * MIB)
    retry = client.post(f"/api/jobs/{job['id']}/retry")
    assert retry.status_code == 200
    assert client.get("/api/system/storage").json()["reserved_bytes"] == needed
    client.post(f"/api/jobs/{retry.json()['id']}/cancel")
    assert client.get("/api/system/storage").json()["reserved_bytes"] == 0


def test_upload_resume_rechecks_changed_disk_and_keeps_partial(client, settings, monkeypatch):
    init = client.post("/api/uploads", json={"filename": "clip.mp4", "size": 6}).json()
    assert client.put(f"/api/uploads/{init['id']}?offset=0", content=b"abc").status_code == 200
    disk(monkeypatch, 1)
    response = client.put(f"/api/uploads/{init['id']}?offset=3", content=b"def")
    assert response.status_code == 507
    assert (settings.tmp_dir / f"upload-{init['id']}.part").read_bytes() == b"abc"
    assert client.get(f"/api/uploads/{init['id']}").json()["received"] == 3


def test_real_upload_transfers_reservation_and_releases_after_ingest(client, samples):
    video = upload(client, samples["a"])
    wait_ready(client, video["id"])
    with client.app.state.sessionmaker() as db:
        assert not list(db.scalars(select(Upload)))
        jobs = list(db.scalars(select(Job).where(Job.kind == "ingest")))
        assert jobs[0].params["storage_bytes"] > 0
        assert snapshot(db, client.app.state.jobs.settings)["reserved_bytes"] == 0


async def run_claimed(manager, job_id):
    with manager.sessionmaker() as db:
        video_ids = list(db.get(Job, job_id).video_ids)
    claimed = await manager._try_claim(job_id, video_ids)
    assert claimed is not None
    await manager._run(*claimed)


def test_worker_rechecks_external_disk_change_before_edit(client, monkeypatch):
    vid = source(client)
    manager = client.app.state.jobs
    manager.settings.workers = 1

    def hold(job_id):
        with manager.sessionmaker() as db:
            job = db.get(Job, job_id)
            if job.status == "queued":
                job.status = "paused"
                db.commit()

    monkeypatch.setattr(manager, "enqueue", hold)
    result = client.post(f"/api/videos/{vid}/edit", json={"edit": {"op": "rotate"}})
    assert result.status_code == 200
    job_id = result.json()["id"]
    called = []

    async def handler(ctx, job):
        called.append(job.id)

    monkeypatch.setitem(manager.handlers, "edit", handler)
    with manager.sessionmaker() as db:
        job = db.get(Job, job_id)
        job.status = "queued"
        db.commit()
    disk(monkeypatch, MIB)
    client.portal.call(run_claimed, manager, job_id)
    final = client.get(f"/api/jobs/{job_id}").json()
    assert final["status"] == "failed"
    assert "磁盘可用空间不足" in final["error"]
    assert not called
    assert client.get("/api/system/storage").json()["reserved_bytes"] == 0


def test_hls_and_old_edit_jobs_compete_with_uploads(client, settings, monkeypatch):
    vid = source(client)
    disk(monkeypatch, 500 * MIB)
    with client.app.state.sessionmaker() as db:
        hls = Job(
            kind="hls", status="paused", params={"estimated_bytes": 300 * MIB}, video_ids=[vid]
        )
        db.add(hls)
        db.commit()
    assert (
        client.post("/api/uploads", json={"filename": "clip.mp4", "size": 50 * MIB}).status_code
        == 507
    )
    with client.app.state.sessionmaker() as db:
        db.delete(db.get(Job, hls.id))
        old = Job(kind="edit", status="paused", params={"edit": {"op": "rotate"}}, video_ids=[vid])
        db.add(old)
        db.commit()
        assert snapshot(db, settings)["reserved_bytes"] > 100 * MIB


def test_ingest_budget_failure_marks_video_retryable(client, monkeypatch):
    vid = source(client)
    manager = client.app.state.jobs
    with manager.sessionmaker() as db:
        video = db.get(Video, vid)
        video.status = "processing"
        job = Job(kind="ingest", params={"storage_bytes": 100 * MIB}, video_ids=[vid])
        db.add(job)
        db.commit()
        job_id = job.id
    disk(monkeypatch, MIB)
    client.portal.call(run_claimed, manager, job_id)
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "failed"
    video = client.get(f"/api/videos/{vid}").json()
    assert video["status"] == "error"
    assert "磁盘可用空间不足" in video["error"]
