"""Presentation timestamps for exact frame navigation and copy-mode cut points."""

from __future__ import annotations

import bisect
import json
import math
from pathlib import Path
from typing import Any

from ..config import Settings
from ..models import new_id
from .ffmpeg import FFmpegError, ProcessHandle, run_command


def parse_timing(data: dict[str, Any]) -> dict[str, list[float]]:
    start = float((data.get("format") or {}).get("start_time") or 0)
    frames, keys = set(), set()
    for frame in data.get("frames") or []:
        raw = frame.get("best_effort_timestamp_time")
        if raw is None:
            continue
        timestamp = round(float(raw) - start, 6)
        if not math.isfinite(timestamp) or timestamp < 0:
            continue
        frames.add(timestamp)
        if frame.get("key_frame") == 1:
            keys.add(timestamp)
    return {"frames": sorted(frames), "keyframes": sorted(keys)}


async def timing_index(
    settings: Settings,
    video_id: str,
    source: Path,
    stream_index: int,
    *,
    keyframes_only: bool = False,
    handle: ProcessHandle | None = None,
) -> dict[str, list[float]]:
    stat = source.stat()
    signature = [str(source), stat.st_size, stat.st_mtime_ns, stream_index]
    folder = settings.derived_dir / video_id
    folder.mkdir(parents=True, exist_ok=True)
    filename = folder / ("keyframes.json" if keyframes_only else "timing.json")
    # A full frame index also satisfies keyframe-only requests.
    for candidate in [folder / "timing.json", filename] if keyframes_only else [filename]:
        try:
            cached = json.loads(candidate.read_text())
            if cached.get("signature") == signature:
                return dict(cached["timing"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    args = [settings.ffprobe, "-v", "error", "-select_streams", str(stream_index)]
    if keyframes_only:
        args += ["-skip_frame", "nokey"]
    args += [
        "-show_frames",
        "-show_format",
        "-show_entries",
        "frame=best_effort_timestamp_time,key_frame:format=start_time",
        "-of",
        "json",
        str(source),
    ]
    result = await run_command(args, handle=handle)
    timing = parse_timing(json.loads(result.stdout))
    if not timing["keyframes"]:
        raise FFmpegError("未找到可用关键帧")
    # Do not cache an index generated while the source was being replaced.
    current = source.stat()
    if (current.st_size, current.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
        raise RuntimeError("视频文件已变化，请重新载入帧索引")
    temporary = folder / f"timing-{new_id()}.tmp"
    try:
        temporary.write_text(json.dumps({"signature": signature, "timing": timing}))
        temporary.replace(filename)
    finally:
        temporary.unlink(missing_ok=True)
    return timing


def snap_cut(
    start: float, end: float, keyframes: list[float], duration: float
) -> tuple[float, float]:
    """Start at the preceding keyframe, end at the following one (or EOF)."""
    before = bisect.bisect_right(keyframes, start + 1e-6) - 1
    after = bisect.bisect_left(keyframes, end - 1e-6)
    actual_start = keyframes[before] if before >= 0 else 0.0
    actual_end = keyframes[after] if after < len(keyframes) else duration
    return actual_start, min(actual_end, duration)
