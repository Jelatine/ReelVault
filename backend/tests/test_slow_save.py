from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from reelvault.media.ffmpeg import Canceled, ProcessHandle, run_command

from .conftest import upload_ready, wait_job


def test_departed_process_group_does_not_abort_cancel(monkeypatch):
    def denied(pid, sig):
        raise PermissionError("departed process group")

    monkeypatch.setattr(os, "killpg", denied)
    proc = SimpleNamespace(pid=12345, returncode=0)
    handle = ProcessHandle(process=proc, process_group=True)
    handle.cancel()
    assert handle.canceled and not handle.paused
    proc.returncode = None
    with pytest.raises(PermissionError):
        handle.cancel()


def slow_worker(tmp_path):
    worker = tmp_path / "slow_disk.py"
    worker.write_text("""
import errno, sys, time
from pathlib import Path
from reelvault.media import transfer_worker as worker
original_open = Path.open
class SlowReader:
    def __init__(self, stream): self.stream = stream
    def __enter__(self): self.stream.__enter__(); return self
    def __exit__(self, *args): return self.stream.__exit__(*args)
    def read(self, size):
        time.sleep(0.03)
        return self.stream.read(size)
def slow_open(path, mode='r', *args, **kwargs):
    stream = original_open(path, mode, *args, **kwargs)
    return SlowReader(stream) if mode == 'rb' else stream
def cross_device(*args): raise OSError(errno.EXDEV, 'simulated cross-device move')
Path.rename = cross_device
Path.open = slow_open
worker.CHUNK = 512
worker.transfer(Path(sys.argv[1]), Path(sys.argv[2]))
""")
    return worker


def install_slow_save(monkeypatch, tmp_path, entered):
    from reelvault.jobs import handlers
    from reelvault.media import transfer

    # Retain the old-path injection: before the fix this reproduces an API stall.
    move = handlers.shutil.move
    worker = slow_worker(tmp_path)
    command = transfer.run_command
    first = True

    def slow_move(src, dst, *args, **kwargs):
        if str(src).endswith("output.mp4"):
            entered.set()
            time.sleep(1.5)
        return move(src, dst, *args, **kwargs)

    async def isolated(args, **kwargs):
        nonlocal first
        if first:
            first = False
            entered.set()
            args = [sys.executable, str(worker), *args[-2:]]
        return await command(args, **kwargs)

    monkeypatch.setattr(handlers.shutil, "move", slow_move)
    monkeypatch.setattr(transfer, "run_command", isolated)


def submit(client, video):
    response = client.post(
        f"/api/videos/{video['id']}/edit",
        json={
            "edit": {"op": "compress", "codec": "h264", "crf": 30},
            "output": {"mode": "new"},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_slow_output_move_keeps_api_responsive(client, samples, monkeypatch, tmp_path):
    """Before the fix this blocks healthz for 1.52 s; the child now owns disk I/O."""
    video = upload_ready(client, samples["a"])
    entered = threading.Event()
    install_slow_save(monkeypatch, tmp_path, entered)
    job = submit(client, video)
    assert entered.wait(10)
    started = time.monotonic()
    assert client.get("/healthz").status_code == 200
    latency = time.monotonic() - started
    assert latency < 0.5, f"Output save blocked the API for {latency:.2f} seconds"
    assert wait_job(client, job["id"])["status"] == "succeeded"


def test_slow_save_progress_pause_cancel_and_other_video_runs_concurrently(
    client, settings, samples, monkeypatch, tmp_path
):
    first = upload_ready(client, samples["a"])
    second = upload_ready(client, samples["b"])
    original = client.get(first["download_url"]).content
    entered = threading.Event()
    install_slow_save(monkeypatch, tmp_path, entered)
    job = submit(client, first)
    assert entered.wait(10)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        data = client.get(f"/api/jobs/{job['id']}").json()
        if data["message"] == "保存结果" and data["progress"] > 0.971:
            break
        time.sleep(0.02)
    assert 0.971 < data["progress"] < 1
    assert client.post(f"/api/jobs/{job['id']}/pause").json()["status"] == "paused"
    time.sleep(0.1)
    partials = list(settings.library_dir.glob(".reelvault-transfer-*.part"))
    assert len(partials) == 1
    size = partials[0].stat().st_size
    time.sleep(0.25)
    assert partials[0].stat().st_size == size
    another = submit(client, second)
    assert wait_job(client, another["id"])["status"] == "succeeded"
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "paused"
    assert client.post(f"/api/jobs/{job['id']}/resume").status_code == 200
    deadline = time.monotonic() + 5
    while partials[0].stat().st_size <= size and time.monotonic() < deadline:
        time.sleep(0.03)
    assert partials[0].stat().st_size > size
    client.post(f"/api/jobs/{job['id']}/cancel")
    canceled = wait_job(client, job["id"])
    assert canceled["status"] == "canceled" and not canceled["result_video_id"]
    assert not partials[0].exists()
    assert client.get(first["download_url"]).content == original


def test_long_diagnostics_and_progress_lines_do_not_abandon_process():
    async def run():
        handle = ProcessHandle()
        progress = []
        result = await asyncio.wait_for(
            run_command(
                [
                    sys.executable,
                    "-c",
                    "import sys;sys.stderr.write('x'*150000+'\\n');"
                    "print('x'*150000);print('out_time_us=1000000',flush=True)",
                ],
                duration=1,
                handle=handle,
                on_progress=progress.append,
            ),
            5,
        )
        assert len(result.stderr) <= 16384 and progress == [1]
        assert handle.process is None and not handle.process_group

    asyncio.run(run())


def test_cancel_kills_wrapper_descendant_that_holds_pipes(tmp_path):
    pid_file = tmp_path / "pid"
    script = tmp_path / "wrapper.py"
    script.write_text("""
import subprocess, sys
code = "import os,sys,time;open(sys.argv[1],'w').write(str(os.getpid()));time.sleep(60)"
subprocess.Popen([sys.executable, '-c', code, sys.argv[1]])
""")

    async def run():
        handle = ProcessHandle()
        task = asyncio.create_task(
            run_command([sys.executable, str(script), str(pid_file)], handle=handle)
        )
        try:
            for _ in range(100):
                if pid_file.exists() and handle.process and handle.process.returncode is not None:
                    break
                await asyncio.sleep(0.02)
            assert pid_file.exists() and not task.done()
            handle.cancel()
            with pytest.raises(Canceled):
                await asyncio.wait_for(task, 3)
            assert handle.process is None
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if pid_file.exists():
                # Some platforms keep an orphan zombie briefly; ensure it cannot run.
                pid = int(pid_file.read_text())
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, 9)

    asyncio.run(run())


def test_shutdown_during_spawn_reaps_the_unregistered_child(monkeypatch):
    async def run():
        launch = asyncio.create_subprocess_exec
        entered = asyncio.Event()
        child = None

        async def delayed(*args, **kwargs):
            nonlocal child
            child = await launch(*args, **kwargs)
            entered.set()
            await asyncio.sleep(0.15)
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
        handle = ProcessHandle()
        task = asyncio.create_task(
            run_command(
                [
                    sys.executable,
                    "-c",
                    "import time;time.sleep(60)",
                ],
                handle=handle,
            )
        )
        await asyncio.wait_for(entered.wait(), 2)
        assert child and handle.process is None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert child.returncode is not None
        assert handle.process is None

    asyncio.run(run())


def test_progress_callback_failure_kills_and_reaps_process(tmp_path):
    pid_file = tmp_path / "callback-pid"

    def fail(_):
        raise ValueError("fixture callback failed")

    async def run():
        handle = ProcessHandle()
        with pytest.raises(ValueError, match="fixture callback"):
            await asyncio.wait_for(
                run_command(
                    [
                        sys.executable,
                        "-c",
                        "import os,sys,time;open(sys.argv[1],'w').write(str(os.getpid()));"
                        "print('out_time_us=1',flush=True);time.sleep(60)",
                        str(pid_file),
                    ],
                    duration=1,
                    handle=handle,
                    on_progress=fail,
                ),
                3,
            )
        assert handle.process is None and not handle.process_group
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)

    asyncio.run(run())


def test_canceled_launch_failure_preserves_cancellation(monkeypatch):
    async def run():
        entered = asyncio.Event()

        async def fail(*args, **kwargs):
            entered.set()
            await asyncio.sleep(0.05)
            raise OSError("fixture launch failure")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fail)
        task = asyncio.create_task(run_command(["fixture"]))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_shutdown_cannot_start_another_worker_after_handler_translates_cancel(settings):
    from .test_job_queue import manager

    async def run():
        entered = asyncio.Event()

        async def handler(ctx, job):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise OSError("fixture launch failure") from None

        jobs = manager(settings, {"test": handler})
        with jobs.sessionmaker() as db:
            jobs.submit(db, "test", {}, [])
        await jobs.start()
        await entered.wait()
        await asyncio.wait_for(jobs.stop(), 2)
        assert not jobs._tasks and not jobs.running

    asyncio.run(run())


def test_real_wrapped_ffmpeg_pause_resume_and_cancel(samples):
    from reelvault.media.ffmpeg import ffmpeg_args

    async def run():
        handle = ProcessHandle()
        values = []
        command = ffmpeg_args(
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
        )
        task = asyncio.create_task(
            run_command(
                [
                    sys.executable,
                    "-c",
                    "import subprocess,sys;subprocess.run(sys.argv[1:],check=True)",
                    *command,
                ],
                duration=4,
                handle=handle,
                on_progress=values.append,
            )
        )
        try:
            for _ in range(100):
                if values and values[-1] > 0.05:
                    break
                await asyncio.sleep(0.02)
            assert values and values[-1] > 0.05
            handle.pause()
            await asyncio.sleep(0.1)
            before = values[:]
            await asyncio.sleep(0.6)
            assert values == before
            handle.resume()
            for _ in range(100):
                if values[-1] > before[-1]:
                    break
                await asyncio.sleep(0.02)
            assert values[-1] > before[-1]
            handle.pause()
            handle.cancel()
            with pytest.raises(Canceled):
                await asyncio.wait_for(task, 2)
        finally:
            if not task.done():
                handle.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_restart_cleans_interrupted_transfer_staging_on_each_storage(settings, tmp_path):
    from fastapi.testclient import TestClient

    from reelvault.locations import register_root
    from reelvault.main import create_app

    settings.ensure_dirs()
    root = tmp_path / "external"
    root.mkdir()
    key, entry = register_root(settings, root, "external")
    settings.storage_locations[key] = entry
    roots = [settings.library_dir, settings.exports_dir, root / "library"]
    for folder in roots:
        (folder / ".reelvault-transfer-dead.part").write_bytes(b"partial output")
        (folder / "retained.mp4").write_bytes(b"original video")
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").status_code == 200
        for folder in roots:
            assert not (folder / ".reelvault-transfer-dead.part").exists()
            assert (folder / "retained.mp4").read_bytes() == b"original video"
