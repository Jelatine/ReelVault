"""Read-only analysis that suggests edit parameters: black bars and silent stretches."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .ffmpeg import (
    FFmpegError,
    FFmpegOperationalError,
    ProcessHandle,
    ProgressCallback,
    run_command,
)
from .probe import MediaInfo

CROP_SAMPLES = 8
CROP_FRAMES = 12
# Ratio of the full range, so 8-bit and 10-bit sources use the same black level.
CROP_LIMIT = 24 / 255
MAX_SEGMENTS = 50


def _analysis(ffmpeg: str, args: list[str]) -> list[str]:
    # Detection filters report at info level; -nostdin/-hide_banner keep the runner's
    # stall detection, fed by -progress since detections can be minutes apart.
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "info",
        "-progress", "pipe:1", "-nostats", *args,
    ]  # fmt: skip


async def detect_crop(
    ffmpeg: str,
    source: Path,
    info: MediaInfo,
    handle: ProcessHandle | None = None,
    on_progress: ProgressCallback | None = None,
) -> dict[str, int] | None:
    """Union of the content found at evenly spaced samples; None without black bars.

    Coordinates use the displayed (auto-rotated) frame, matching crop edits.
    """
    left, top, right, bottom = info.width, info.height, 0, 0
    found = False
    for i in range(CROP_SAMPLES):
        time = info.duration * (i + 0.5) / CROP_SAMPLES
        result = await run_command(
            _analysis(
                ffmpeg,
                [
                    "-ss", f"{time:.3f}", "-i", str(source),
                    "-map", f"0:{info.video_index}", "-an", "-sn", "-dn",
                    "-frames:v", str(CROP_FRAMES),
                    "-vf", f"cropdetect=limit={CROP_LIMIT:.6f}:round=2:reset=0",
                    "-f", "null", "-",
                ],
            ),
            handle=handle,
            timeout=60,
        )  # fmt: skip
        if on_progress:
            on_progress((i + 1) / CROP_SAMPLES)
        crops = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", result.stderr)
        if not crops:
            continue
        w, h, x, y = (int(v) for v in crops[-1])
        # A black or near-black sample (fade, title card) says nothing about the frame.
        if w < info.width * 0.2 or h < info.height * 0.2:
            continue
        found = True
        left, top = min(left, x), min(top, y)
        right, bottom = max(right, x + w), max(bottom, y + h)
    if not found:
        return None
    width, height = (right - left) // 2 * 2, (bottom - top) // 2 * 2
    if width >= info.width - 2 and height >= info.height - 2:
        return None
    return {"x": left, "y": top, "width": width, "height": height}


async def detect_silence(
    ffmpeg: str,
    source: Path,
    info: MediaInfo,
    *,
    noise_db: float,
    min_duration: float,
    handle: ProcessHandle | None = None,
    on_progress: ProgressCallback | None = None,
) -> list[tuple[float, float]]:
    """Silent intervals of the primary audio track on the container timeline."""
    if info.audio_index is None:
        return []
    result = await run_command(
        _analysis(
            ffmpeg,
            [
                "-i", str(source), "-map", f"0:{info.audio_index}", "-vn", "-sn", "-dn",
                "-af", f"silencedetect=noise={noise_db}dB:duration={min_duration}",
                "-f", "null", "-",
            ],
        ),
        handle=handle,
        duration=info.duration,
        on_progress=on_progress,
    )  # fmt: skip
    silences: list[tuple[float, float]] = []
    start: float | None = None
    for kind, value in re.findall(r"silence_(start|end): (-?[\d.]+)", result.stderr):
        if kind == "start":
            start = max(0.0, float(value))
        elif start is not None:
            silences.append((start, min(float(value), info.duration)))
            start = None
    if start is not None:
        silences.append((start, info.duration))
    return [(s, e) for s, e in silences if e > s]


def keep_segments(
    silences: list[tuple[float, float]], duration: float, padding: float
) -> list[dict[str, float]]:
    """Complement of the silences, keeping `padding` seconds of each silence edge.

    Silence at the very start or end of the file is removed without padding.
    """
    segments: list[dict[str, float]] = []
    position = 0.0
    for start, end in silences:
        cut_start = 0.0 if start <= 0.05 else start + padding
        cut_end = duration if end >= duration - 0.05 else end - padding
        if cut_end - cut_start <= 0.05:
            continue
        if cut_start - position > 0.05:
            segments.append({"start": round(position, 3), "end": round(cut_start, 3)})
        position = cut_end
    if duration - position > 0.05:
        segments.append({"start": round(position, 3), "end": round(duration, 3)})
    return segments


INTEGRITY_SAMPLE = 20


async def check_integrity(
    ffmpeg: str,
    source: Path,
    info: MediaInfo,
    handle: ProcessHandle | None = None,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Decode the video and audio tracks completely and collect the decoder errors."""
    streams = ["-map", f"0:{info.video_index}"]
    if info.audio_index is not None:
        streams += ["-map", f"0:{info.audio_index}"]
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-progress", "pipe:1", "-nostats", "-i", str(source), *streams, "-f", "null", "-",
    ]  # fmt: skip
    try:
        result = await run_command(
            args, handle=handle, duration=info.duration, on_progress=on_progress
        )
        output, failed = result.stderr, False
    except FFmpegOperationalError:
        raise
    except FFmpegError as error:
        # Decoding stopped early: the remaining file could not be read at all.
        output, failed = str(error), True
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return {
        "errors": len(lines) + (1 if failed and not lines else 0),
        "aborted": failed,
        "sample": [line[:300] for line in lines[:INTEGRITY_SAMPLE]],
    }
