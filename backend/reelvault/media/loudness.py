"""First pass of two-pass EBU R128 loudness normalisation."""

from __future__ import annotations

import json
import math
import re

from .ffmpeg import ProcessHandle, ProgressCallback, run_command
from .ops import LOUDNESS_KEYS


def parse_loudness(stderr: str) -> dict[str, float] | None:
    """loudnorm's JSON summary; None when absent or unusable (e.g. silence is -inf)."""
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr)
    if not blocks:
        return None
    try:
        data = json.loads(blocks[-1])
        values = {key: float(data[key]) for key in LOUDNESS_KEYS}
    except (ValueError, KeyError, TypeError):
        return None
    return values if all(math.isfinite(v) for v in values.values()) else None


async def measure_loudness(
    ffmpeg: str,
    args: list[str],
    *,
    duration: float,
    handle: ProcessHandle | None = None,
    on_progress: ProgressCallback | None = None,
) -> dict[str, float] | None:
    # loudnorm prints its measurement at info level; keep the -hide_banner/-nostdin
    # pair so the runner still applies stall detection and thread limits.
    command = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "info"]
    command += ["-progress", "pipe:1", "-nostats", *args]
    result = await run_command(command, duration=duration, on_progress=on_progress, handle=handle)
    return parse_loudness(result.stderr)
