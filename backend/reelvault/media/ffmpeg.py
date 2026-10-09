from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import os
import re
import shutil
import signal
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .windows_process import BELOW_NORMAL_PRIORITY_CLASS, CREATE_SUSPENDED, WindowsJob

ProgressCallback = Callable[[float], None]


class FFmpegError(RuntimeError):
    pass


class Canceled(Exception):
    pass


@dataclass
class ProcessHandle:
    """Lets a caller cancel the ffmpeg process that is currently running."""

    process: asyncio.subprocess.Process | None = None
    canceled: bool = False
    paused: bool = False
    process_group: bool = False
    # Niceness for background work, so serving pages and video keeps the CPU first.
    nice: int = 0
    _resumed: asyncio.Event = field(default_factory=asyncio.Event)
    _windows_job: WindowsJob | None = field(default=None, repr=False)

    def _signal(self, signal_number: int) -> None:
        proc = self.process
        if proc:
            with contextlib.suppress(ProcessLookupError):
                if self.process_group:
                    # A wrapper may have exited while descendants still own the pipes.
                    try:
                        os.killpg(proc.pid, signal_number)
                    except PermissionError:
                        # Darwin can report EPERM for a departed process group. A
                        # completed child must not abort cancellation or shutdown;
                        # permission failures for a live child remain actionable.
                        if proc.returncode is None:
                            # The child watcher can reap the PID before asyncio's
                            # returncode callback runs. Probe without sending a
                            # signal; ESRCH is handled by the enclosing suppress.
                            os.kill(proc.pid, 0)
                            raise
                elif proc.returncode is None:
                    proc.send_signal(signal_number)

    def pause(self) -> None:
        if self.paused:
            return
        if self._windows_job:
            self._windows_job.suspend(True)
        elif os.name != "nt":
            self._signal(signal.SIGSTOP)
        self.paused = True
        self._resumed.clear()

    def resume(self) -> None:
        if self.paused:
            if self._windows_job:
                self._windows_job.suspend(False)
            elif os.name != "nt":
                self._signal(signal.SIGCONT)
        self.paused = False
        self._resumed.set()

    async def checkpoint(self) -> None:
        while self.paused and not self.canceled:
            await self._resumed.wait()
        if self.canceled:
            raise Canceled()

    def cancel(self) -> None:
        self.canceled = True
        # Wake a task paused between subprocesses as well as killing stopped processes.
        self.paused = False
        self._resumed.set()
        self.kill_process()

    def kill_process(self) -> None:
        """Terminate the owned tree without changing the user's cancellation flag."""
        if self._windows_job:
            self._windows_job.kill()
        elif os.name == "nt":
            if self.process and self.process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self.process.kill()
        else:
            self._signal(signal.SIGKILL)

    def release_process(self) -> None:
        if self._windows_job:
            self._windows_job.close()
            self._windows_job = None
        self.process = None
        self.process_group = False


@functools.cache
def _nice_command() -> str | None:
    return shutil.which("nice")


async def start_process(
    args: list[str], handle: ProcessHandle, **kwargs: Any
) -> asyncio.subprocess.Process:
    """Launch an owned tree; a Windows child stays suspended until attached."""
    await handle.checkpoint()
    windows_job = WindowsJob() if os.name == "nt" else None
    options: dict[str, Any]
    if windows_job:
        flags = CREATE_SUSPENDED | (BELOW_NORMAL_PRIORITY_CLASS if handle.nice > 0 else 0)
        options = {"creationflags": flags}
    else:
        options = {"start_new_session": True}
        # nice execs the program in place: same PID, and every thread inherits the
        # priority. A missing program still raises FileNotFoundError without it.
        nice = _nice_command() if handle.nice > 0 else None
        if nice and shutil.which(args[0], path=(kwargs.get("env") or {}).get("PATH")):
            args = [nice, "-n", str(handle.nice), *args]
    launch = asyncio.create_task(
        asyncio.create_subprocess_exec(args[0], *args[1:], **kwargs, **options)
    )
    cancellation = None
    try:
        try:
            proc = await asyncio.shield(launch)
        except asyncio.CancelledError as error:
            cancellation = error
            try:
                proc = await launch
            except Exception:
                raise cancellation from None
        handle.process = proc
        handle.process_group = windows_job is None
        try:
            if windows_job:
                windows_job.attach(proc.pid)
                handle._windows_job = windows_job
            if cancellation or handle.canceled:
                handle.kill_process()
            elif windows_job:
                if not handle.paused:
                    windows_job.suspend(False)
            elif handle.paused:
                handle._signal(signal.SIGSTOP)
        except BaseException:
            # Assignment can fail: the suspended child must still be reaped.
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.communicate()
            handle.release_process()
            raise
        if cancellation:
            await proc.communicate()
            handle.release_process()
            raise cancellation
        return proc
    except BaseException:
        if windows_job:
            windows_job.close()
        raise


@dataclass
class RunResult:
    stdout: bytes
    stderr: str
    extra: dict[str, Any] = field(default_factory=dict)


async def run_command(
    args: list[str],
    *,
    duration: float = 0,
    on_progress: ProgressCallback | None = None,
    handle: ProcessHandle | None = None,
    cwd: Path | None = None,
) -> RunResult:
    """Run ffmpeg/ffprobe. When `duration` is set, ffmpeg's -progress output on stdout is
    parsed into a 0..1 fraction and reported through `on_progress`."""
    control = handle or ProcessHandle()
    track = on_progress is not None and duration > 0
    if cwd is not None and "/" in args[0] and not Path(args[0]).is_absolute():
        args = [str(Path(args[0]).resolve()), *args[1:]]
    proc = await start_process(
        args,
        control,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    assert proc.stdout and proc.stderr

    stderr_tail = bytearray()
    stdout_chunks: list[bytes] = []

    async def read_stderr() -> None:
        assert proc.stderr
        # readlines has a 64 KiB limit; a long diagnostic used to abandon the child.
        while chunk := await proc.stderr.read(8192):
            stderr_tail.extend(chunk)
            del stderr_tail[:-16384]

    async def read_stdout() -> None:
        assert proc.stdout
        if not track:
            stdout_chunks.append(await proc.stdout.read())
            return
        pending = b""
        dropping = False
        while chunk := await proc.stdout.read(8192):
            pending += chunk
            while b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
                if dropping:
                    dropping = False
                    continue
                line = raw.decode(errors="replace").strip()
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    value = line.split("=", 1)[1]
                    if value.isdigit() and on_progress:
                        on_progress(min(1.0, int(value) / 1_000_000 / duration))
            if len(pending) > 8192:
                pending = b""
                dropping = True

    readers = [asyncio.create_task(read_stderr()), asyncio.create_task(read_stdout())]
    try:
        await asyncio.gather(*readers)
        code = await proc.wait()
    except BaseException:
        # Clean up on parser/callback failures as well as task cancellation. Kill the
        # whole session, including children that inherit stdout/stderr from wrappers.
        control.kill_process()
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        await proc.communicate()
        raise
    finally:
        if control.process is proc:
            control.release_process()
    if handle:
        if handle.canceled:
            raise Canceled()
        await handle.checkpoint()
    tail = stderr_tail.decode(errors="replace")

    if code != 0:
        tail = tail.strip()
        raise FFmpegError(tail[-2000:] or f"{args[0]} exited with code {code}")
    return RunResult(stdout=b"".join(stdout_chunks), stderr=tail)


def ffmpeg_args(ffmpeg: str, args: list[str], *, progress: bool = True) -> list[str]:
    base = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    if progress:
        base += ["-progress", "pipe:1", "-nostats"]
    return base + args


async def ffprobe_json(
    ffprobe: str, path: str, handle: ProcessHandle | None = None
) -> dict[str, Any]:
    result = await run_command(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        handle=handle,
    )
    data: dict[str, Any] = json.loads(result.stdout or b"{}")
    return data


async def ffmpeg_version(ffmpeg: str) -> str:
    try:
        result = await run_command([ffmpeg, "-version"])
    except (FFmpegError, FileNotFoundError):
        return "unavailable"
    first = result.stdout.decode(errors="replace").splitlines()[:1]
    m = re.match(r"ffmpeg version (\S+)", first[0] if first else "")
    return m.group(1) if m else "unknown"
