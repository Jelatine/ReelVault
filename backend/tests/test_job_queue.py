from __future__ import annotations

import asyncio
import sqlite3
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from reelvault.config import Settings
from reelvault.db import make_engine, make_sessionmaker
from reelvault.jobs.handlers import edit
from reelvault.jobs.manager import JobContext, JobManager
from reelvault.media.ffmpeg import Canceled, ProcessHandle, ffmpeg_args, run_command
from reelvault.migrate import upgrade
from reelvault.models import Job, JobLog, utcnow
from tests.conftest import upload_ready, wait_job, wait_ready


def manager(settings: Settings, handlers=None) -> JobManager:
    settings.ensure_dirs()
    engine = make_engine(settings.db_path)
    upgrade(engine)
    return JobManager(settings, make_sessionmaker(engine), handlers or {})


def test_priority_fifo_pause_persistence_and_restart(settings: Settings) -> None:
    settings.workers = 1
    seen = []

    async def handler(ctx: JobContext, job: Job) -> None:
        seen.append(job.id)

    m = manager(settings, {"test": handler})
    with m.sessionmaker() as db:
        low = Job(kind="test", priority=0, video_ids=[])
        normal = Job(kind="test", priority=1, video_ids=[])
        high = Job(kind="test", priority=2, video_ids=[])
        high2 = Job(kind="test", priority=2, video_ids=[])
        paused = Job(kind="test", status="paused", video_ids=[])
        interrupted = Job(kind="test", status="paused", started_at=utcnow(), video_ids=[])
        rows = [low, normal, high, high2, paused, interrupted]
        created = utcnow()
        for index, row in enumerate(rows):
            # Windows clocks can return identical timestamps for adjacent inserts.
            row.created_at = created + timedelta(microseconds=index)
        db.add_all(rows)
        db.commit()
        ids = [j.id for j in (low, normal, high, high2, paused, interrupted)]

    async def run() -> None:
        await m.start()
        try:
            await m.wait_idle()
            assert seen == [ids[2], ids[3], ids[1], ids[0]]
            with m.sessionmaker() as db:
                assert db.get(Job, ids[4]).status == "paused"
                assert db.get(Job, ids[5]).status == "failed"
                m.resume(db, db.get(Job, ids[4]))
            await m.wait_idle()
            assert seen[-1] == ids[4]
        finally:
            await m.stop()

    asyncio.run(run())


def test_overlapping_inputs_serialize_but_independent_jobs_run(settings: Settings) -> None:
    settings.workers = 2
    entered = []
    release = asyncio.Event()

    async def handler(ctx: JobContext, job: Job) -> None:
        entered.append(job.id)
        if job.params.get("block"):
            await release.wait()

    m = manager(settings, {"test": handler})
    with m.sessionmaker() as db:
        jobs = m.submit_many(
            db,
            [
                ("test", {"block": True}, ["a", "b"]),
                ("test", {}, ["b", "c"]),
                ("test", {}, ["d"]),
            ],
        )
        created = utcnow()
        for index, job in enumerate(jobs):
            job.created_at = created + timedelta(microseconds=index)
        db.commit()
        ids = [j.id for j in jobs]
        assert m.describe(db, jobs[0])["conflicting_jobs"] == [ids[1]]

    async def run() -> None:
        await m.start()
        try:
            for _ in range(100):
                if ids[2] in entered:
                    break
                await asyncio.sleep(0.01)
            assert entered == [ids[0], ids[2]]
            release.set()
            await m.wait_idle()
            assert entered == [ids[0], ids[2], ids[1]]
        finally:
            await m.stop()

    asyncio.run(run())


def test_real_process_pause_resume_cancel_and_between_steps(samples: dict[str, Path]) -> None:
    async def run() -> None:
        values = []
        handle = ProcessHandle()
        task = asyncio.create_task(
            run_command(
                ffmpeg_args(
                    "ffmpeg",
                    [
                        "-re",
                        "-i",
                        str(samples["a"]),
                        "-c:v",
                        "libx264",
                        "-f",
                        "null",
                        "-",
                    ],
                ),
                duration=4,
                handle=handle,
                on_progress=values.append,
            )
        )
        try:
            for _ in range(100):
                if values and values[-1] > 0.05:
                    break
                await asyncio.sleep(0.05)
            assert handle.process is not None and values[-1] > 0.05
            pid = handle.process.pid
            handle.pause()
            await asyncio.sleep(0.15)  # Drain any progress already buffered before SIGSTOP.
            before = list(values)
            await asyncio.sleep(0.7)
            assert values == before and not task.done()
            handle.resume()
            assert handle.process.pid == pid
            await asyncio.wait_for(task, 10)
            assert values[-1] >= 0.95
        finally:
            handle.cancel()
            if not task.done():
                with pytest.raises(Canceled):
                    await task

        handle = ProcessHandle()
        handle.pause()
        blocked = asyncio.create_task(
            run_command([sys.executable, "-c", "print('ok')"], handle=handle)
        )
        await asyncio.sleep(0.05)
        assert handle.process is None and not blocked.done()
        handle.cancel()
        with pytest.raises(Canceled):
            await asyncio.wait_for(blocked, 1)

        handle = ProcessHandle()
        blocked = asyncio.create_task(
            run_command([sys.executable, "-c", "import time; time.sleep(30)"], handle=handle)
        )
        while handle.process is None:
            await asyncio.sleep(0.01)
        handle.pause()
        handle.cancel()
        with pytest.raises(Canceled):
            await asyncio.wait_for(blocked, 1)

    asyncio.run(run())


def test_eta_resets_on_regression_and_pause(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [0.0]
    monkeypatch.setattr("reelvault.jobs.manager.time.monotonic", lambda: clock[0])
    m = manager(settings)
    with m.sessionmaker() as db:
        job = Job(kind="test", status="running", video_ids=[])
        db.add(job)
        db.commit()
        job_id = job.id
    ctx = JobContext(m, job_id)
    clock[0] = 10
    ctx.set_progress(0.25)
    with m.sessionmaker() as db:
        assert db.get(Job, job_id).eta_seconds == 30
    clock[0] = 20
    ctx.set_progress(0.1)
    with m.sessionmaker() as db:
        assert db.get(Job, job_id).eta_seconds is None
        job = db.get(Job, job_id)
        job.status = "paused"
        db.commit()
    clock[0] = 30
    ctx.set_progress(0.3)
    with m.sessionmaker() as db:
        assert db.get(Job, job_id).eta_seconds is None
        job = db.get(Job, job_id)
        job.status = "running"
        db.commit()
    ctx.reset_rate()
    clock[0] = 40
    ctx.set_progress(0.4)
    with m.sessionmaker() as db:
        assert db.get(Job, job_id).eta_seconds == pytest.approx(60)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX niceness")
def test_job_processes_run_at_lower_priority(settings: Settings) -> None:
    show = [sys.executable, "-c", "import os; print(os.nice(0))"]

    async def run() -> None:
        base = int((await run_command(show)).stdout)
        handle = JobContext(manager(settings), "job").handle
        assert handle.nice == settings.job_nice == 10
        lowered = int((await run_command(show, handle=handle)).stdout)
        assert lowered == min(19, base + 10)
        with pytest.raises(FileNotFoundError):
            await run_command(["reelvault-missing-program"], handle=handle)

    asyncio.run(run())


def test_locked_database_does_not_stall_event_loop_or_cancel(settings: Settings) -> None:
    settings.workers = 1
    started = asyncio.Event()

    async def handler(ctx: JobContext, job: Job) -> None:
        for step in range(10_000):
            ctx.check_canceled()
            ctx.set_progress(step / 10_000, f"step {step}")
            started.set()
            await asyncio.sleep(0.01)

    m = manager(settings, {"test": handler})
    with m.sessionmaker() as db:
        job = Job(kind="test", video_ids=[])
        db.add(job)
        db.commit()
        job_id = job.id

    async def run() -> None:
        await m.start()
        try:
            await asyncio.wait_for(started.wait(), 5)
            # Another writer holds the lock, as a slow bind mount or long transaction would.
            lock = sqlite3.connect(settings.db_path, isolation_level=None)
            lock.execute("BEGIN IMMEDIATE")
            try:
                worst = 0.0
                deadline = time.monotonic() + 1.5
                while time.monotonic() < deadline:
                    before = time.monotonic()
                    await asyncio.sleep(0.02)
                    worst = max(worst, time.monotonic() - before)
                assert worst < 0.5
                with m.sessionmaker() as db:
                    m.cancel(db, db.get(Job, job_id))
            finally:
                lock.rollback()
                lock.close()
            await m.wait_idle(10)
            with m.sessionmaker() as db:
                assert db.get(Job, job_id).status == "canceled"
        finally:
            await m.stop()

    asyncio.run(run())


def test_failed_edit_retry_real_output_and_invalid_controls(
    client: TestClient, samples: dict[str, Path]
) -> None:
    video = upload_ready(client, samples["a"])
    m = client.app.state.jobs

    async def fail(ctx: JobContext, job: Job) -> None:
        raise RuntimeError("临时编码失败")

    m.handlers = {**m.handlers, "edit": fail}
    response = client.post(
        f"/api/videos/{video['id']}/edit",
        json={
            "edit": {"op": "rotate", "angle": 90},
            "priority": 2,
        },
    )
    assert response.status_code == 200, response.text
    old = wait_job(client, response.json()["id"])
    assert old["status"] == "failed" and old["priority"] == 2
    for action in ("pause", "resume", "cancel"):
        r = client.post(f"/api/jobs/{old['id']}/{action}")
        assert r.status_code == (200 if action == "cancel" else 409)
    assert client.put(f"/api/jobs/{old['id']}/priority", json={"priority": 3}).status_code == 422
    assert client.put(f"/api/jobs/{old['id']}/priority", json={"priority": 0}).status_code == 409
    m.handlers["edit"] = edit
    r = client.post(f"/api/jobs/{old['id']}/retry")
    assert r.status_code == 200, r.text
    new = wait_job(client, r.json()["id"])
    assert new["status"] == "succeeded", new
    assert new["retry_of"] == old["id"] and new["priority"] == 2
    output = wait_ready(client, new["result_video_id"])
    assert (output["width"], output["height"]) == (240, 320)
    assert client.get(f"/api/jobs/{old['id']}").json()["status"] == "failed"
    assert client.post(f"/api/jobs/{new['id']}/retry").status_code == 409
    old_logs = client.get(f"/api/jobs/{old['id']}/logs").json()
    assert [(e["level"], e["message"]) for e in old_logs] == [
        ("info", "已提交"),
        ("info", "开始处理"),
        ("error", "临时编码失败"),
    ]
    new_logs = [e["message"] for e in client.get(f"/api/jobs/{new['id']}/logs").json()]
    assert new_logs[0] == f"重试失败任务 {old['id'][:8]}" and new_logs[-1] == "完成"
    assert [v["id"] for v in new["videos"]] == [video["id"], new["result_video_id"]]
    assert new["videos"][0] == {"id": video["id"], "title": video["title"], "deleted": False}


def test_job_logs_record_stages_and_are_cleared_with_jobs(client: TestClient) -> None:
    m = client.app.state.jobs

    async def staged(ctx: JobContext, job: Job) -> None:
        ctx.set_progress(0.1, "第一步")
        ctx.set_progress(0.2, "第一步")
        ctx.log("回退提示", "warning")
        ctx.set_progress(0.5, "第二步")

    m.handlers = {**m.handlers, "staged": staged}
    with m.sessionmaker() as db:
        job_id = m.submit(db, "staged", {}, []).id
    assert wait_job(client, job_id)["status"] == "succeeded"
    logs = client.get(f"/api/jobs/{job_id}/logs").json()
    assert [(e["level"], e["message"]) for e in logs] == [
        ("info", "已提交"),
        ("info", "开始处理"),
        ("info", "第一步"),
        ("warning", "回退提示"),
        ("info", "第二步"),
        ("info", "完成"),
    ]
    assert client.get("/api/jobs/missing/logs").status_code == 404
    assert client.delete("/api/jobs").json()["deleted"] >= 1
    with m.sessionmaker() as db:
        assert not db.scalars(select(JobLog).where(JobLog.job_id == job_id)).all()


def test_api_queued_pause_priority_resume_cancel(client: TestClient) -> None:
    m = client.app.state.jobs
    with m.sessionmaker() as db:
        job = Job(kind="unknown", status="paused", video_ids=[])
        db.add(job)
        db.commit()
        job_id = job.id
    assert client.put(f"/api/jobs/{job_id}/priority", json={"priority": 2}).json()["priority"] == 2
    assert client.get("/api/system/info").json()["paused_jobs"] == 1
    assert client.post(f"/api/jobs/{job_id}/resume").status_code == 200
    assert wait_job(client, job_id)["status"] == "failed"
    with m.sessionmaker() as db:
        job = Job(kind="unknown", status="paused", video_ids=[])
        db.add(job)
        db.commit()
        job_id = job.id
    assert client.post(f"/api/jobs/{job_id}/cancel").json()["status"] == "canceled"


def test_api_running_pause_resume_cancel_and_duplicate_retry(
    client: TestClient, samples: dict[str, Path]
) -> None:
    video = upload_ready(client, samples["a"])
    m = client.app.state.jobs

    async def slow(ctx: JobContext, job: Job) -> None:
        await run_command(
            ffmpeg_args(
                "ffmpeg",
                [
                    "-re",
                    "-i",
                    str(samples["a"]),
                    "-c:v",
                    "libx264",
                    "-f",
                    "null",
                    "-",
                ],
            ),
            duration=4,
            handle=ctx.handle,
            on_progress=ctx.set_progress,
        )

    m.handlers = {**m.handlers, "edit": slow}
    job = client.post(
        f"/api/videos/{video['id']}/edit", json={"edit": {"op": "rotate", "angle": 90}}
    ).json()
    job_id = job["id"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == "running" and job["progress"] > 0.05:
            break
        time.sleep(0.05)
    assert job["status"] == "running" and job["progress"] > 0.05
    assert client.post(f"/api/jobs/{job_id}/pause").json()["status"] == "paused"
    time.sleep(0.2)
    paused = client.get(f"/api/jobs/{job_id}").json()
    time.sleep(0.7)
    after = client.get(f"/api/jobs/{job_id}").json()
    assert after["status"] == "paused" and after["progress"] == paused["progress"]
    assert after["eta_seconds"] is None
    info = client.get("/api/system/info").json()
    assert info["paused_jobs"] == 1 and info["running_jobs"] == 0
    assert client.post(f"/api/jobs/{job_id}/resume").json()["status"] == "running"
    assert client.post(f"/api/jobs/{job_id}/pause").status_code == 200
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    assert wait_job(client, job_id)["status"] == "canceled"

    with m.sessionmaker() as db:
        failed = Job(kind="edit", status="failed", params=job["params"], video_ids=[video["id"]])
        db.add(failed)
        db.commit()
        failed_id = failed.id
    first = client.post(f"/api/jobs/{failed_id}/retry")
    assert first.status_code == 200
    assert client.post(f"/api/jobs/{failed_id}/retry").status_code == 409
    assert client.post(f"/api/jobs/{first.json()['id']}/cancel").status_code == 200
    assert wait_job(client, first.json()["id"])["status"] == "canceled"


def test_migration_preserves_existing_job(settings: Settings) -> None:
    from alembic import command
    from sqlalchemy import text

    from reelvault.migrate import alembic_config

    settings.ensure_dirs()
    engine = make_engine(settings.db_path)
    cfg = alembic_config(str(engine.url))
    command.upgrade(cfg, "0008")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO jobs (id,kind,status,params,video_ids,progress,message,created_at) "
                "VALUES ('legacy','edit','queued','{}','[\"a\"]',0,'排队中',CURRENT_TIMESTAMP)"
            )
        )
    upgrade(engine)
    with make_sessionmaker(engine)() as db:
        job = db.get(Job, "legacy")
        assert job.status == "queued" and job.video_ids == ["a"] and job.priority == 1
        assert job.eta_seconds is None and job.retry_of is None
    engine.dispose()
