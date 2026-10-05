from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
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

    def cancel(self) -> None:
        self.canceled = True
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
) -> RunResult:
    """Run ffmpeg/ffprobe. When `duration` is set, ffmpeg's -progress output on stdout is
    parsed into a 0..1 fraction and reported through `on_progress`."""
    if handle and handle.canceled:
        raise Canceled()
    track = on_progress is not None and duration > 0
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if handle:
        handle.process = proc
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

    await asyncio.gather(read_stderr(), read_stdout())
    code = await proc.wait()
    if handle:
        handle.process = None
        if handle.canceled:
            raise Canceled()
    if code != 0:
        tail = "".join(stderr_tail).strip()
        raise FFmpegError(tail[-2000:] or f"{args[0]} exited with code {code}")
    return RunResult(stdout=b"".join(stdout_chunks), stderr="".join(stderr_tail))


def ffmpeg_args(ffmpeg: str, args: list[str], *, progress: bool = True) -> list[str]:
    base = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    if progress:
        base += ["-progress", "pipe:1", "-nostats"]
    return base + args


async def ffprobe_json(ffprobe: str, path: str) -> dict[str, Any]:
    result = await run_command(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]
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
