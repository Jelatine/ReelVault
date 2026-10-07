"""Atomic publication of large outputs without blocking the ASGI event loop."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

from .ffmpeg import ProcessHandle, ProgressCallback, run_command


async def move_output(
    source: Path,
    destination: Path,
    *,
    handle: ProcessHandle,
    on_progress: ProgressCallback | None = None,
) -> None:
    staged = destination.parent / f".reelvault-transfer-{uuid4().hex}.part"
    try:
        await run_command(
            [sys.executable, "-m", "reelvault.media.transfer_worker", str(source), str(staged)],
            duration=1,
            handle=handle,
            on_progress=on_progress,
        )
        await handle.checkpoint()
        # Publish after the last checkpoint, so cancellation can't expose a partial file.
        staged.replace(destination)
    finally:
        await asyncio.to_thread(staged.unlink, missing_ok=True)
