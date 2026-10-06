from __future__ import annotations

import asyncio
import contextlib
import json
import re
import signal
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    _resumed: asyncio.Event = field(default_factory=asyncio.Event)

    def pause(self) -> None:
        self.paused = True
        self._resumed.clear()
        if self.process and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.send_signal(signal.SIGSTOP)

    def resume(self) -> None:
        self.paused = False
        self._resumed.set()
        if self.process and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.send_signal(signal.SIGCONT)

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
        if self.process and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.kill()


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
    if handle:
        await handle.checkpoint()
    track = on_progress is not None and duration > 0
    if cwd is not None and "/" in args[0] and not Path(args[0]).is_absolute():
        args = [str(Path(args[0]).resolve()), *args[1:]]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    if handle:
        handle.process = proc
        if handle.canceled:
            handle.cancel()
        elif handle.paused:
            handle.pause()
    assert proc.stdout and proc.stderr

    stderr_tail: list[str] = []
    stdout_chunks: list[bytes] = []

    async def read_stderr() -> None:
        assert proc.stderr
        async for line in proc.stderr:
            stderr_tail.append(line.decode(errors="replace"))
            if len(stderr_tail) > 40:
                del stderr_tail[0]

    async def read_stdout() -> None:
        assert proc.stdout
        if not track:
            stdout_chunks.append(await proc.stdout.read())
            return
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").strip()
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                value = line.split("=", 1)[1]
                if value.isdigit() and on_progress:
                    on_progress(min(1.0, int(value) / 1_000_000 / duration))

    try:
        await asyncio.gather(read_stderr(), read_stdout())
        code = await proc.wait()
    except asyncio.CancelledError:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        await proc.wait()
        raise
    if handle:
        handle.process = None
        if handle.canceled:
            raise Canceled()
        await handle.checkpoint()
    if code != 0:
        tail = "".join(stderr_tail).strip()
        raise FFmpegError(tail[-2000:] or f"{args[0]} exited with code {code}")
    return RunResult(stdout=b"".join(stdout_chunks), stderr="".join(stderr_tail))


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
