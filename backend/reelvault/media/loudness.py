"""Measurement passes for audio processing: EBU R128 loudness and noise floor."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .ffmpeg import ProcessHandle, ProgressCallback, ffmpeg_args, run_command
from .ops import LOUDNESS_KEYS, noise_floor_command
from .probe import MediaInfo


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
    # loudnorm prints its measurement at info level.
    command = ffmpeg_args(ffmpeg, args, loglevel="info")
    result = await run_command(command, duration=duration, on_progress=on_progress, handle=handle)
    return parse_loudness(result.stderr)


# afftdn accepts noise floors in this range.
NOISE_FLOOR_RANGE = (-80.0, -20.0)


def parse_noise_floor(text: str) -> float | None:
    """10th percentile of window RMS levels: the quiet parts between sounds."""
    levels = sorted(
        value for raw in re.findall(r"RMS_level=(\S+)", text) if math.isfinite(value := float(raw))
    )
    if not levels:
        return None
    low, high = NOISE_FLOOR_RANGE
    return max(low, min(high, levels[len(levels) // 10]))


async def measure_noise_floor(
    ffmpeg: str,
    source: tuple[Path, MediaInfo],
    folder: Path,
    *,
    handle: ProcessHandle | None = None,
    on_progress: ProgressCallback | None = None,
) -> float | None:
    """None for digital silence or when there is nothing to measure."""
    output = folder / "noise-floor.txt"
    command = ffmpeg_args(ffmpeg, noise_floor_command(source, output.name))
    await run_command(
        command, duration=source[1].duration, on_progress=on_progress, handle=handle, cwd=folder
    )
    try:
        return parse_noise_floor(output.read_text())
    finally:
        output.unlink(missing_ok=True)
