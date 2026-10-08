"""Versioned content fingerprints; visual matches are review candidates, not proof."""

from __future__ import annotations

import asyncio
import hashlib
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .ffmpeg import ProcessHandle, ProgressCallback, ffmpeg_args, run_command
from .probe import MediaInfo

ALGORITHM = 1
SAMPLES = 8


class SourceChanged(RuntimeError):
    pass


def file_signature(path: Path) -> list[Any]:
    from ..object_types import OriginalPath

    if isinstance(path, OriginalPath):
        ref = path.object_ref
        return [
            f"s3/{path.parent.name}/{ref.key}",
            "s3",
            ref.version_id or ref.etag,
            ref.size,
            ref.sha256,
            1,
        ]
    stat = path.stat()
    return [
        str(path.resolve()),
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    ]


async def content_hash(
    path: Path,
    *,
    handle: ProcessHandle | None = None,
    on_progress: ProgressCallback | None = None,
) -> tuple[str, list[Any]]:
    signature = file_signature(path)
    digest = hashlib.sha256()
    size = signature[3]
    read = 0
    with path.open("rb") as stream:
        while True:
            if handle:
                await handle.checkpoint()
            chunk = await asyncio.to_thread(stream.read, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            read += len(chunk)
            if on_progress:
                on_progress(min(1, read / max(1, size)))
    if file_signature(path) != signature or read != size:
        raise SourceChanged("Video file changed during fingerprinting")
    from ..object_types import OriginalPath

    if isinstance(path, OriginalPath) and digest.hexdigest() != path.object_ref.sha256:
        raise SourceChanged("Cached S3 original checksum does not match its pinned object")
    return digest.hexdigest(), signature


@dataclass(frozen=True)
class FramePrint:
    dhash: str
    color: tuple[float, float, float]


def frame_print(rgb: bytes) -> FramePrint:
    if len(rgb) != 9 * 8 * 3:
        raise ValueError("Expected one 9 by 8 RGB frame")
    pixels = list(zip(rgb[::3], rgb[1::3], rgb[2::3], strict=True))
    gray = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in pixels]
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | int(gray[row * 9 + col + 1] > gray[row * 9 + col])
    color = tuple(round(sum(pixel[c] for pixel in pixels) / 72, 3) for c in range(3))
    return FramePrint(f"{bits:016x}", (color[0], color[1], color[2]))


@dataclass(frozen=True)
class VisualPrint:
    duration: float
    aspect: float
    frames: tuple[FramePrint, ...]
    algorithm: int = ALGORITHM

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisualPrint:
        return cls(
            duration=data["duration"],
            aspect=data["aspect"],
            algorithm=data["algorithm"],
            frames=tuple(FramePrint(f["dhash"], tuple(f["color"])) for f in data["frames"]),
        )


async def visual_fingerprint(
    ffmpeg: str,
    source: Path,
    info: MediaInfo,
    *,
    handle: ProcessHandle | None = None,
    on_progress: ProgressCallback | None = None,
) -> VisualPrint:
    signature = file_signature(source)
    if not math.isfinite(info.duration) or info.duration <= 0 or not info.height:
        raise ValueError("Video duration and dimensions are required")
    frames: list[FramePrint] = []
    # Seek within the actual video interval; avoid the first/last frame and title-only matches.
    start = max(0.0, info.video_delay)
    end = info.duration
    if math.isfinite(info.video_duration) and info.video_duration > 0:
        end = min(end, start + info.video_duration)
    span = end - start
    if span <= 0:
        raise ValueError("Video timeline is empty")
    for index in range(SAMPLES):
        timestamp = start + span * (index + 0.5) / SAMPLES
        result = await run_command(
            ffmpeg_args(
                ffmpeg,
                [
                    "-ss",
                    f"{timestamp:.9f}",
                    "-i",
                    str(source.resolve()),
                    "-map",
                    f"0:{info.video_index}",
                    "-an",
                    "-sn",
                    "-dn",
                    "-vf",
                    "scale=9:8:flags=area",
                    "-frames:v",
                    "1",
                    "-pix_fmt",
                    "rgb24",
                    "-f",
                    "rawvideo",
                    "pipe:1",
                ],
                progress=False,
            ),
            handle=handle,
        )
        frames.append(frame_print(result.stdout))
        if on_progress:
            on_progress((index + 1) / SAMPLES)
    if file_signature(source) != signature:
        raise SourceChanged("Video file changed during fingerprinting")
    return VisualPrint(info.duration, info.width / info.height, tuple(frames))


def visual_similarity(left: VisualPrint, right: VisualPrint) -> float | None:
    """Return a 0..1 candidate score; never group through transitive similarity."""
    if left.algorithm != ALGORITHM or right.algorithm != ALGORITHM:
        return None
    if len(left.frames) != SAMPLES or len(right.frames) != SAMPLES:
        return None
    if any(
        not math.isfinite(v) or v <= 0
        for v in (
            left.duration,
            right.duration,
            left.aspect,
            right.aspect,
        )
    ):
        return None
    if abs(left.duration - right.duration) > max(0.15, min(left.duration, right.duration) * 0.02):
        return None
    if abs(left.aspect - right.aspect) / min(left.aspect, right.aspect) > 0.02:
        return None
    distances: list[int] = []
    colors: list[float] = []
    for a, b in zip(left.frames, right.frames, strict=True):
        distances.append((int(a.dhash, 16) ^ int(b.dhash, 16)).bit_count())
        colors.append(max(abs(x - y) for x, y in zip(a.color, b.color, strict=True)))
    mean_distance = sum(distances) / SAMPLES
    mean_color = sum(colors) / SAMPLES
    if mean_distance > 6 or max(distances) > 12 or mean_color > 14 or max(colors) > 30:
        return None
    return round(1 - max(mean_distance / 64, mean_color / 255), 4)
