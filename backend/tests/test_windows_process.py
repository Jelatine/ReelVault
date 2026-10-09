from __future__ import annotations

import asyncio
import os
import sys

import pytest

from reelvault.media.ffmpeg import Canceled, ProcessHandle, run_command
from reelvault.media.windows_process import WindowsJob

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows process-tree ownership")


def test_departed_wrapper_children_pause_resume_cancel_and_next_command(tmp_path):
    heartbeat = tmp_path / "heartbeat"
    child = tmp_path / "child.py"
    child.write_text(
        "import sys,time\n"
        "with open(sys.argv[1], 'ab', buffering=0) as f:\n"
        " while True:\n"
        "  f.write(b'x');time.sleep(0.02)\n"
    )
    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text(
        "import subprocess,sys\nsubprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]])\n"
    )

    async def run():
        handle = ProcessHandle()
        task = asyncio.create_task(
            run_command([sys.executable, str(wrapper), str(child), str(heartbeat)], handle=handle)
        )
        try:
            async with asyncio.timeout(5):
                while not (
                    heartbeat.exists() and handle.process and handle.process.returncode is not None
                ):
                    await asyncio.sleep(0.02)
            assert not task.done()
            handle.pause()
            handle.pause()  # Repeated controls must not increase a native suspend count.
            await asyncio.sleep(0.1)
            before = heartbeat.stat().st_size
            await asyncio.sleep(0.2)
            assert heartbeat.stat().st_size == before
            handle.resume()
            handle.resume()
            async with asyncio.timeout(2):
                while heartbeat.stat().st_size == before:
                    await asyncio.sleep(0.02)
            handle.pause()
            handle.cancel()
            with pytest.raises(Canceled):
                await asyncio.wait_for(task, 2)
            assert handle.process is None and handle._windows_job is None
            stopped = heartbeat.stat().st_size
            await asyncio.sleep(0.1)
            assert heartbeat.stat().st_size == stopped
            result = await run_command([sys.executable, "-c", "print('next task')"])
            assert result.stdout.strip() == b"next task"
        finally:
            handle.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_job_assignment_failure_reaps_suspended_child(monkeypatch):
    async def run():
        spawned = []
        launch = asyncio.create_subprocess_exec

        async def capture(*args, **kwargs):
            proc = await launch(*args, **kwargs)
            spawned.append(proc)
            return proc

        def fail(self, pid):
            raise OSError("fixture job assignment failed")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
        monkeypatch.setattr(WindowsJob, "attach", fail)
        handle = ProcessHandle()
        with pytest.raises(OSError, match="fixture job assignment"):
            await asyncio.wait_for(
                run_command([sys.executable, "-c", "import time;time.sleep(60)"], handle=handle), 2
            )
        assert len(spawned) == 1 and spawned[0].returncode is not None
        assert handle.process is None and handle._windows_job is None

    asyncio.run(run())
