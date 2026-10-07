"""Derived assets: poster, hover-preview clip, scrubbing sprite + WebVTT, playable copy."""

from __future__ import annotations

import math
from pathlib import Path

from .encoding import EncoderRuntime
from .ffmpeg import ProcessHandle, ProgressCallback, ffmpeg_args, run_command
from .probe import MediaInfo

POSTER_WIDTH = 640
PREVIEW_WIDTH = 320
PREVIEW_SEGMENTS = 6
PREVIEW_SEGMENT_SECONDS = 1.0
SPRITE_THUMB_WIDTH = 160
SPRITE_COLUMNS = 10
SPRITE_MAX_THUMBS = 100

POSTER = "poster.jpg"
PREVIEW = "preview.mp4"
SPRITE = "sprite.jpg"
VTT = "thumbnails.vtt"
PLAYABLE = "playable.mp4"

# mjpeg needs full-range YUV; newer ffmpeg refuses to encode limited range.
JPEG = ["-pix_fmt", "yuvj420p", "-q:v", "4"]


def even(n: float) -> int:
    return max(2, int(round(n / 2)) * 2)


def scaled_height(info: MediaInfo, width: int) -> int:
    if not info.width or not info.height:
        return even(width * 9 / 16)
    return even(width * info.height / info.width)


def default_cover_time(info: MediaInfo) -> float:
    return round(min(info.duration * 0.1, 10.0), 3) if info.duration > 1 else 0.0


async def extract_frame(
    ffmpeg: str,
    src: Path,
    info: MediaInfo,
    out: Path,
    time: float,
    width: int | None = POSTER_WIDTH,
    handle: ProcessHandle | None = None,
) -> None:
    time = max(0.0, min(time, max(info.duration - 0.05, 0.0)))
    vf = f"scale={width}:-2" if width and info.width > width else "null"
    args = [
        "-ss", f"{time:.3f}", "-i", str(src),
        "-map", f"0:{info.video_index}", "-frames:v", "1",
        "-vf", vf, *JPEG, str(out),
    ]  # fmt: skip
    await run_command(ffmpeg_args(ffmpeg, args, progress=False), handle=handle)


async def make_preview(
    ffmpeg: str,
    src: Path,
    info: MediaInfo,
    out: Path,
    handle: ProcessHandle | None = None,
    *,
    encoding: EncoderRuntime | None = None,
    encoder: str = "software",
) -> dict[str, object] | None:
    """Short silent montage of evenly spaced 1s segments, used for hover previews."""
    seg = PREVIEW_SEGMENT_SECONDS
    h = scaled_height(info, PREVIEW_WIDTH)
    vf = f"scale={PREVIEW_WIDTH}:{h},setsar=1,fps=24,format=yuv420p"
    if info.duration <= PREVIEW_SEGMENTS * seg * 1.5:
        args = [
            "-i", str(src), "-t", f"{min(info.duration, PREVIEW_SEGMENTS * seg):.3f}",
            "-map", f"0:{info.video_index}", "-vf", vf,
        ]  # fmt: skip
    else:
        args = []
        step = info.duration / PREVIEW_SEGMENTS
        for i in range(PREVIEW_SEGMENTS):
            start = step * i + (step - seg) / 2
            args += ["-ss", f"{start:.3f}", "-t", f"{seg:.3f}", "-i", str(src)]
        chains = [f"[{i}:{info.video_index}]{vf}[v{i}]" for i in range(PREVIEW_SEGMENTS)]
        inputs = "".join(f"[v{i}]" for i in range(PREVIEW_SEGMENTS))
        graph = ";".join(chains) + f";{inputs}concat=n={PREVIEW_SEGMENTS}:v=1:a=0[out]"
        args += ["-filter_complex", graph, "-map", "[out]"]
    args += [
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
        "-movflags", "+faststart", str(out),
    ]  # fmt: skip
    if encoding:
        return await encoding.run([args], encoder, handle=handle)
    await run_command(ffmpeg_args(ffmpeg, args, progress=False), handle=handle)
    return None


def sprite_layout(duration: float) -> tuple[float, int]:
    """Return (interval seconds, thumbnail count)."""
    if duration <= 0:
        return 1.0, 1
    interval = max(1.0, duration / SPRITE_MAX_THUMBS)
    count = max(1, min(SPRITE_MAX_THUMBS, math.ceil(duration / interval)))
    return interval, count


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def build_vtt(duration: float, interval: float, count: int, tw: int, th: int, url: str) -> str:
    lines = ["WEBVTT", ""]
    for i in range(count):
        start = i * interval
        end = min((i + 1) * interval, duration) if i < count - 1 else duration
        if end <= start:
            end = start + interval
        x = (i % SPRITE_COLUMNS) * tw
        y = (i // SPRITE_COLUMNS) * th
        lines += [f"{_ts(start)} --> {_ts(end)}", f"{url}#xywh={x},{y},{tw},{th}", ""]
    return "\n".join(lines)


async def make_sprite(
    ffmpeg: str,
    src: Path,
    info: MediaInfo,
    out_dir: Path,
    handle: ProcessHandle | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    interval, count = sprite_layout(info.duration)
    tw = SPRITE_THUMB_WIDTH
    th = scaled_height(info, tw)
    rows = math.ceil(count / SPRITE_COLUMNS)
    cols = min(SPRITE_COLUMNS, count)
    # Sample decoded frames uniformly: sparse keyframes may omit entire scenes or even
    # produce no image. One interval of cloned tail frames completes the final tile,
    # including clips shorter than the sampling interval. Only indexed cells are used.
    vf = (
        f"tpad=stop_mode=clone:stop_duration={interval:.6f},"
        f"fps=1/{interval:.6f}:start_time=0,scale={tw}:{th},setsar=1,tile={cols}x{rows}"
    )
    args = [
        "-i", str(src),
        "-map", f"0:{info.video_index}", "-an", "-vf", vf,
        "-frames:v", "1", *JPEG, str(out_dir / SPRITE),
    ]  # fmt: skip
    await run_command(
        ffmpeg_args(ffmpeg, args),
        duration=info.duration,
        on_progress=on_progress,
        handle=handle,
    )
    (out_dir / VTT).write_text(build_vtt(info.duration, interval, count, tw, th, SPRITE))


async def make_playable(
    ffmpeg: str,
    src: Path,
    info: MediaInfo,
    out: Path,
    handle: ProcessHandle | None = None,
    on_progress: ProgressCallback | None = None,
    *,
    encoding: EncoderRuntime | None = None,
    encoder: str = "software",
) -> dict[str, object] | None:
    """Create an H.264/AAC MP4 for sources browsers can't play. Remux when possible."""
    args = ["-i", str(src), "-map", f"0:{info.video_index}"]
    if info.audio_index is not None:
        args += ["-map", f"0:{info.audio_index}"]
    if info.remuxable_to_mp4:
        args += ["-c", "copy"]
    else:
        args += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k",
        ]  # fmt: skip
    args += ["-movflags", "+faststart", "-f", "mp4", str(out)]
    if encoding:
        return await encoding.run(
            [args], encoder, duration=info.duration, on_progress=on_progress, handle=handle
        )
    await run_command(
        ffmpeg_args(ffmpeg, args), duration=info.duration, on_progress=on_progress, handle=handle
    )
    return None
