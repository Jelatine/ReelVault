"""Objective quality of an encode against its source: VMAF when compiled, else SSIM."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .color import needs_tone_mapping, source_filter, tone_device_args
from .ffmpeg import ProcessHandle, ProgressCallback, run_command
from .probe import MediaInfo


def metric(filters: set[str] | None) -> str:
    return "vmaf" if filters and "libvmaf" in filters else "ssim"


def quality_graph(reference: MediaInfo, encoded: MediaInfo, name: str, log: str) -> str:
    """[0] is the encode, [1] the source, normalised to the encode's size and rate."""
    normalise = source_filter(reference)
    graph = (
        f"[0:{encoded.video_index}]settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p[encoded];"
        f"[1:{reference.video_index}]" + (normalise + "," if normalise else "") + "settb=AVTB,"
        f"setpts=PTS-STARTPTS,fps={encoded.fps or reference.fps or 25:.6f},"
        f"scale={encoded.width}:{encoded.height}:flags=bicubic,format=yuv420p[source];"
    )
    if name == "vmaf":
        # Every fifth frame keeps the cost close to one extra decode of both files.
        threads = max(1, min(8, os.cpu_count() or 1))
        options = f"n_subsample=5:n_threads={threads}:log_fmt=json:log_path={log}"
        return graph + f"[encoded][source]libvmaf={options}"
    return graph + "[encoded][source]ssim"


def parse_quality(name: str, stderr: str, log: Path) -> float | None:
    if name == "vmaf":
        try:
            pooled = json.loads(log.read_text())["pooled_metrics"]["vmaf"]["mean"]
            return round(float(pooled), 2)
        except (OSError, ValueError, KeyError, TypeError):
            match = re.search(r"VMAF score: ([\d.]+)", stderr)
            return round(float(match.group(1)), 2) if match else None
    match = re.search(r"SSIM .*All:([\d.]+)", stderr)
    return round(float(match.group(1)), 4) if match else None


async def measure_quality(
    ffmpeg: str,
    encoded: tuple[Path, MediaInfo],
    reference: tuple[Path, MediaInfo],
    name: str,
    *,
    handle: ProcessHandle | None = None,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any] | None:
    """{"metric", "score"}; None when the score could not be read."""
    with tempfile.TemporaryDirectory(prefix="reelvault-quality-") as directory:
        log = Path(directory) / "vmaf.json"
        graph = quality_graph(reference[1], encoded[1], name, log.name)
        device = tone_device_args() if needs_tone_mapping(reference[1]) else []
        args = [
            ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "info",
            "-progress", "pipe:1", "-nostats", *device,
            "-i", str(encoded[0]), "-i", str(reference[0]),
            "-lavfi", graph, "-an", "-f", "null", "-",
        ]  # fmt: skip
        result = await run_command(
            args,
            handle=handle,
            duration=encoded[1].duration,
            on_progress=on_progress,
            cwd=Path(directory),
        )
        score = parse_quality(name, result.stderr, log)
    return None if score is None else {"metric": name, "score": score}
