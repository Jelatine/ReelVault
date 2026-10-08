"""Scene-change analysis on the original video presentation timeline."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .ffmpeg import ProcessHandle, ProgressCallback, ffmpeg_args, run_command
from .probe import MediaInfo


class SceneParams(BaseModel):
    threshold: float = Field(0.4, ge=0.01, le=1, allow_inf_nan=False)
    min_interval: float = Field(1, ge=0.05, le=600, allow_inf_nan=False)


def source_signature(source: Path) -> list[Any]:
    from ..object_types import OriginalPath

    if isinstance(source, OriginalPath):
        ref = source.object_ref
        return [
            source.name,
            ref.size,
            f"s3:{source.parent.name}:{ref.sha256}:{ref.etag}:{ref.version_id}",
        ]
    stat = source.stat()
    return [source.name, stat.st_size, stat.st_mtime_ns]


def read_cuts(path: Path, duration: float, min_interval: float) -> list[dict[str, float]]:
    cuts: list[dict[str, float]] = []
    timestamp: float | None = None
    if path.stat().st_size > 32 * 1024 * 1024:
        raise RuntimeError("场景候选点过多，请提高检测阈值")
    with path.open() as stream:
        for line in stream:
            match = re.search(r"\bpts_time:(\S+)", line)
            if match:
                timestamp = float(match.group(1))
            elif line.startswith("lavfi.scene_score=") and timestamp is not None:
                score = float(line.split("=", 1)[1])
                if not math.isfinite(timestamp) or not math.isfinite(score):
                    continue
                previous = cuts[-1]["time"] if cuts else 0
                # Avoid tiny first/last chapters and clusters of adjacent detections.
                if (
                    timestamp - previous + 1e-6 < min_interval
                    or duration - timestamp + 1e-6 < min_interval
                ):
                    continue
                cuts.append({"time": round(timestamp, 6), "score": score})
                if len(cuts) > 10000:
                    raise RuntimeError("场景候选点超过 10000 个，请提高阈值或最小间隔")
    return cuts


async def detect_scenes(
    ffmpeg: str,
    source: Path,
    info: MediaInfo,
    params: SceneParams,
    temp: Path,
    *,
    handle: ProcessHandle | None = None,
    on_progress: ProgressCallback | None = None,
) -> list[dict[str, float]]:
    # Normalize video PTS while keeping its delay relative to the container start.
    metadata = temp / "scenes.txt"
    vf = (
        f"setpts=PTS-STARTPTS+{info.video_delay:.9f}/TB,"
        f"select='gt(scene,{params.threshold:.9f})',"
        "metadata=mode=print:key=lavfi.scene_score:file=scenes.txt"
    )
    await run_command(
        ffmpeg_args(
            ffmpeg,
            [
                "-i",
                str(source.resolve()),
                "-map",
                f"0:{info.video_index}",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                vf,
                "-fps_mode",
                "vfr",
                "-f",
                "null",
                "-",
            ],
        ),
        duration=info.duration,
        handle=handle,
        on_progress=on_progress,
        cwd=temp,
    )
    return read_cuts(metadata, info.duration, params.min_interval)


def scene_chapters(cuts: list[dict[str, float]], duration: float) -> list[dict[str, Any]]:
    starts = [0.0, *[cut["time"] for cut in cuts]]
    return [
        {
            "start": start,
            "end": starts[i + 1] if i + 1 < len(starts) else duration,
            "title": f"场景 {i + 1}",
        }
        for i, start in enumerate(starts)
    ]
