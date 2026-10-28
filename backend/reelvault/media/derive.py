"""Derived assets: poster, hover-preview clip, scrubbing sprite + WebVTT, playable copy."""

from __future__ import annotations

import contextlib
import json
import math
import re
import shutil
import tempfile
from pathlib import Path

from .color import sdr_commands
from .encoding import EncoderRuntime
from .ffmpeg import FFmpegError, ProcessHandle, ProgressCallback, ffmpeg_args, run_command
from .probe import MediaInfo

POSTER_WIDTH = 640
PREVIEW_WIDTH = 320
PREVIEW_SEGMENTS = 6
PREVIEW_SEGMENT_SECONDS = 1.0
SPRITE_THUMB_WIDTH = 160
SPRITE_COLUMNS = 10
SPRITE_MAX_THUMBS = 100
# Shorter clips decode quickly; probing keyframes would cost more than it saves.
SPRITE_SEEK_AFTER = 120  # seconds

POSTER = "poster.jpg"
PREVIEW = "preview.mp4"
SPRITE = "sprite.jpg"
VTT = "thumbnails.vtt"
WAVEFORM = "waveform.json"
WAVEFORM_BINS = 1000
PLAYABLE = "playable.mp4"

# Decoder complaints about a damaged stream (e.g. a zero-filled region), as opposed to
# operational failures such as a full disk or a missing filter.
DECODE_ERRORS = (
    "Error while decoding stream",
    "Error splitting the input into NAL units",
    "Invalid NAL unit size",
)


def is_decode_error(error: FFmpegError) -> bool:
    return any(marker in str(error) for marker in DECODE_ERRORS)


# mjpeg needs full-range YUV; newer ffmpeg refuses to encode limited range.
JPEG = ["-pix_fmt", "yuvj420p", "-q:v", "4"]


def even(n: float) -> int:
    return max(2, int(round(n / 2)) * 2)


def scaled_height(info: MediaInfo, width: int) -> int:
    if not info.width or not info.height:
        return even(width * 9 / 16)
    return even(width * info.height / info.width)


def parse_peaks(text: str) -> list[float]:
    """Linear 0-1 peak per window from astats Peak_level (dBFS) metadata."""
    peaks = []
    for raw in re.findall(r"Peak_level=(\S+)", text):
        try:
            level = float(raw)
        except ValueError:
            continue
        peaks.append(round(min(1.0, 10 ** (level / 20)), 3) if math.isfinite(level) else 0.0)
    return peaks


async def make_waveform(
    ffmpeg: str,
    src: Path,
    info: MediaInfo,
    out: Path,
    handle: ProcessHandle | None = None,
    on_progress: ProgressCallback | None = None,
) -> bool:
    """Peak envelope of the primary audio track for the trim timeline; False without audio."""
    if info.audio_index is None or info.duration <= 0:
        return False
    rate = 48000
    window = max(1, math.ceil(info.duration * rate / WAVEFORM_BINS))
    with tempfile.TemporaryDirectory(prefix="reelvault-waveform-") as directory:
        stats = Path(directory) / "peaks.txt"
        af = (
            f"aresample={rate},asetnsamples=n={window}:p=0,"
            "astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=Peak_level,"
            f"ametadata=mode=print:key=lavfi.astats.Overall.Peak_level:file={stats.name}"
        )
        args = ["-i", str(src), "-map", f"0:{info.audio_index}", "-vn", "-sn", "-dn"]
        args += ["-af", af, "-f", "null", "-"]
        await run_command(
            ffmpeg_args(ffmpeg, args),
            duration=info.duration,
            handle=handle,
            on_progress=on_progress,
            cwd=Path(directory),
        )
        peaks = parse_peaks(stats.read_text())
    out.write_text(json.dumps({"duration": info.duration, "peaks": peaks}, separators=(",", ":")))
    return True


def default_cover_time(info: MediaInfo) -> float:
    return round(min(info.duration * 0.1, 10.0), 3) if info.duration > 1 else 0.0


# Automatic covers sample a few seconds from the default time (then later points) and
# keep the most detailed frame that is neither black nor flat.
COVER_WINDOW = 3.0
COVER_SAMPLE_FPS = 4
COVER_MIN_LUMA = 40  # 8-bit average; limited-range black is 16
COVER_MIN_SPREAD = 24  # YHIGH - YLOW: fades, titles on flat colour and black stay below


def parse_cover_stats(text: str, offset: float) -> list[tuple[float, float, float]]:
    """(time, average luma, luma spread) per sampled frame of signalstats metadata."""
    samples: list[tuple[float, float, float]] = []
    current: dict[str, float] = {}

    def flush() -> None:
        if {"time", "YAVG", "YLOW", "YHIGH"} <= current.keys():
            samples.append(
                (current["time"] + offset, current["YAVG"], current["YHIGH"] - current["YLOW"])
            )

    for line in text.splitlines():
        if line.startswith("frame:"):
            flush()
            current = {}
            for field in line.split():
                if field.startswith("pts_time:"):
                    with contextlib.suppress(ValueError):
                        current["time"] = float(field.split(":", 1)[1])
        elif line.startswith("lavfi.signalstats."):
            key, _, value = line.removeprefix("lavfi.signalstats.").partition("=")
            with contextlib.suppress(ValueError):
                current[key] = float(value)
    flush()
    return samples


def pick_cover(samples: list[tuple[float, float, float]]) -> float | None:
    usable = [s for s in samples if s[1] >= COVER_MIN_LUMA and s[2] >= COVER_MIN_SPREAD]
    if not usable:
        return None
    # Most detail wins; the earliest of equally detailed frames.
    return max(usable, key=lambda s: (s[2], -s[0]))[0]


async def choose_cover_time(
    ffmpeg: str, src: Path, info: MediaInfo, handle: ProcessHandle | None = None
) -> float:
    """Automatic cover time that avoids black and flat frames near the default time."""
    start = default_cover_time(info)
    if info.duration <= 0:
        return start
    windows = [start, info.duration * 0.25, info.duration * 0.5]
    with tempfile.TemporaryDirectory(prefix="reelvault-cover-") as directory:
        stats = Path(directory) / "cover-stats.txt"
        for window in dict.fromkeys(round(w, 3) for w in windows if w < info.duration):
            vf = (
                f"fps={COVER_SAMPLE_FPS},scale=160:-2,format=yuv420p,signalstats,"
                f"metadata=mode=print:file={stats.name}"
            )
            args = ["-ss", f"{window:.3f}", "-i", str(src), "-t", str(COVER_WINDOW)]
            args += ["-map", f"0:{info.video_index}", "-vf", vf, "-f", "null", "-"]
            args = sdr_commands([args], [(src, info)])[0]
            try:
                await run_command(
                    ffmpeg_args(ffmpeg, args, progress=False), handle=handle, cwd=Path(directory)
                )
                chosen = pick_cover(parse_cover_stats(stats.read_text(), window))
            except (FFmpegError, OSError):
                return start
            if chosen is not None:
                return round(min(chosen, max(info.duration - 0.05, 0.0)), 3)
    return start


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
    args = sdr_commands([args], [(src, info)])[0]
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
        for start in preview_starts(info.duration):
            args += ["-ss", f"{start:.3f}", "-t", f"{seg:.3f}", "-i", str(src)]
        chains = [f"[{i}:{info.video_index}]{vf}[v{i}]" for i in range(PREVIEW_SEGMENTS)]
        inputs = "".join(f"[v{i}]" for i in range(PREVIEW_SEGMENTS))
        graph = ";".join(chains) + f";{inputs}concat=n={PREVIEW_SEGMENTS}:v=1:a=0[out]"
        args += ["-filter_complex", graph, "-map", "[out]"]
    args += [*PREVIEW_ENCODE, str(out)]
    args = sdr_commands([args], [(src, info)])[0]
    try:
        if encoding:
            return await encoding.run([args], encoder, handle=handle)
        await run_command(ffmpeg_args(ffmpeg, args, progress=False), handle=handle)
    except FFmpegError as error:
        # One damaged segment pushes the montage over FFmpeg's decode error rate
        # (exit 69); rebuild it from the segments that still decode.
        if info.duration <= PREVIEW_SEGMENTS * seg * 1.5 or not is_decode_error(error):
            raise
        out.unlink(missing_ok=True)
        await _preview_by_segments(ffmpeg, src, info, out, vf, handle)
    return None


PREVIEW_ENCODE = [
    "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-movflags", "+faststart",
]  # fmt: skip


def preview_starts(duration: float) -> list[float]:
    step = duration / PREVIEW_SEGMENTS
    return [step * i + (step - PREVIEW_SEGMENT_SECONDS) / 2 for i in range(PREVIEW_SEGMENTS)]


async def _preview_by_segments(
    ffmpeg: str,
    src: Path,
    info: MediaInfo,
    out: Path,
    vf: str,
    handle: ProcessHandle | None,
) -> None:
    """Encode each preview segment on its own, skip those that cannot be decoded and
    join the rest without re-encoding (identical encoder settings)."""
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        parts: list[Path] = []
        for i, start in enumerate(preview_starts(info.duration)):
            part = Path(tmp) / f"{i}.mp4"
            args = [
                "-ss", f"{start:.3f}", "-t", f"{PREVIEW_SEGMENT_SECONDS:.3f}", "-i", str(src),
                "-map", f"0:{info.video_index}", "-vf", vf, *PREVIEW_ENCODE, str(part),
            ]  # fmt: skip
            args = sdr_commands([args], [(src, info)])[0]
            try:
                await run_command(ffmpeg_args(ffmpeg, args, progress=False), handle=handle)
            except FFmpegError as error:
                if not is_decode_error(error):
                    raise
                part.unlink(missing_ok=True)
            if part.is_file() and part.stat().st_size:
                parts.append(part)
            elif handle and handle.on_log:
                handle.on_log(
                    f"预览片段 {i + 1}/{PREVIEW_SEGMENTS}（{start:.3f} 秒）无法解码，已跳过",
                    "warning",
                )
        if not parts:
            raise FFmpegError("无法生成预览片段：所有采样位置均无法解码有效视频帧")
        listing = Path(tmp) / "parts.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in parts))
        args = [
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c", "copy", "-movflags", "+faststart", str(out),
        ]  # fmt: skip
        await run_command(ffmpeg_args(ffmpeg, args, progress=False), handle=handle)


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


async def max_keyframe_gap(
    ffprobe: str,
    src: Path,
    info: MediaInfo,
    window: float,
    handle: ProcessHandle | None = None,
) -> float:
    """Largest keyframe spacing seen in three windows across the video; inf when a
    window holds fewer than two keyframes. Reads packets only, nothing is decoded."""
    gap = 0.0
    for fraction in (0.1, 0.5, 0.9):
        start = max(0.0, info.duration * fraction - window / 2)
        result = await run_command(
            [
                ffprobe, "-v", "error", "-select_streams", str(info.video_index),
                "-read_intervals", f"{start:.3f}%+{window:.3f}",
                "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(src),
            ],
            handle=handle,
        )  # fmt: skip
        keys: list[float] = []
        for line in result.stdout.decode(errors="replace").splitlines():
            pts, _, flags = line.partition(",")
            if "K" in flags:
                with contextlib.suppress(ValueError):
                    keys.append(float(pts))
        keys.sort()
        if len(keys) < 2:
            return math.inf
        gap = max(gap, *(b - a for a, b in zip(keys, keys[1:], strict=False)))
    return gap


async def _sprite_by_seeking(
    ffmpeg: str,
    src: Path,
    info: MediaInfo,
    out: Path,
    interval: float,
    count: int,
    vf: str,
    tile: str,
    handle: ProcessHandle | None,
    on_progress: ProgressCallback | None,
) -> None:
    """One input-side seek per thumbnail. Each decodes at most one GOP, so a long
    video costs ~count GOPs instead of every frame."""
    last = max(0.0, (info.video_duration or info.duration) - 0.1)
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        frames = Path(tmp)
        previous: Path | None = None
        missing: list[Path] = []
        for i in range(count):
            frame = frames / f"{i:04d}.png"
            args = [
                "-ss", f"{min(i * interval, last):.3f}", "-i", str(src),
                "-map", f"0:{info.video_index}", "-an", "-frames:v", "1",
                "-vf", vf, str(frame),
            ]  # fmt: skip
            args = sdr_commands([args], [(src, info)])[0]
            try:
                await run_command(ffmpeg_args(ffmpeg, args, progress=False), handle=handle)
            except FFmpegError as error:
                # A damaged GOP must not discard the other usable thumbnails.
                # Keep cancellation and operational errors (disk, filters, etc.) fatal.
                if not is_decode_error(error):
                    raise
                frame.unlink(missing_ok=True)
            if not frame.is_file() or not frame.stat().st_size:
                if handle and handle.on_log:
                    handle.on_log(
                        f"缩略图 {i + 1}/{count}（{min(i * interval, last):.3f} 秒）"
                        "无法解码，将使用相邻有效帧",
                        "warning",
                    )
                if previous:
                    shutil.copyfile(previous, frame)
                else:
                    missing.append(frame)
            else:
                # The first GOP may be damaged too; backfill from the first good frame.
                for pending in missing:
                    shutil.copyfile(frame, pending)
                missing.clear()
                previous = frame
            if on_progress:
                on_progress((i + 1) / count)
        if previous is None:
            raise FFmpegError("无法生成进度条缩略图：所有采样位置均无法解码有效视频帧")
        args = [
            "-framerate", "1", "-i", str(frames / "%04d.png"),
            "-vf", tile, "-frames:v", "1", *JPEG, str(out),
        ]  # fmt: skip
        await run_command(ffmpeg_args(ffmpeg, args, progress=False), handle=handle)


async def make_sprite(
    ffmpeg: str,
    src: Path,
    info: MediaInfo,
    out_dir: Path,
    handle: ProcessHandle | None = None,
    on_progress: ProgressCallback | None = None,
    *,
    ffprobe: str | None = None,
) -> None:
    interval, count = sprite_layout(info.duration)
    tw = SPRITE_THUMB_WIDTH
    th = scaled_height(info, tw)
    rows = math.ceil(count / SPRITE_COLUMNS)
    cols = min(SPRITE_COLUMNS, count)
    scale = f"scale={tw}:{th},setsar=1"
    tile = f"tile={cols}x{rows}"
    # Seeking is far cheaper than decoding everything when keyframes are closer
    # together than thumbnails; with sparse keyframes every seek would decode from
    # the distant previous keyframe, so those fall back to a single full decode.
    if (
        ffprobe
        and info.duration >= SPRITE_SEEK_AFTER
        and await max_keyframe_gap(ffprobe, src, info, max(2 * interval, 20.0), handle) <= interval
    ):
        await _sprite_by_seeking(
            ffmpeg, src, info, out_dir / SPRITE, interval, count, scale, tile, handle, on_progress
        )
    else:
        # Sample decoded frames uniformly: sparse keyframes may omit entire scenes or even
        # produce no image. One interval of cloned tail frames completes the final tile,
        # including clips shorter than the sampling interval. Only indexed cells are used.
        vf = (
            f"tpad=stop_mode=clone:stop_duration={interval:.6f},"
            f"fps=1/{interval:.6f}:start_time=0,{scale},{tile}"
        )
        args = [
            "-i", str(src),
            "-map", f"0:{info.video_index}", "-an", "-vf", vf,
            "-frames:v", "1", *JPEG, str(out_dir / SPRITE),
        ]  # fmt: skip
        args = sdr_commands([args], [(src, info)])[0]
        await run_command(
            ffmpeg_args(ffmpeg, args),
            duration=info.duration,
            on_progress=on_progress,
            handle=handle,
        )
    # Fixed LF: Windows would otherwise write CRLF and break naive cue splitting.
    vtt = build_vtt(info.duration, interval, count, tw, th, SPRITE)
    (out_dir / VTT).write_text(vtt, newline="\n")


async def make_hevc_playable(
    ffmpeg: str,
    src: Path,
    info: MediaInfo,
    out: Path,
    handle: ProcessHandle | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Remux SDR HEVC into an hvc1 MP4 (browsers reject hev1 and Matroska)."""
    args = ["-i", str(src), "-map", f"0:{info.video_index}"]
    if info.audio_index is not None:
        args += ["-map", f"0:{info.audio_index}"]
    args += ["-c", "copy", "-tag:v", "hvc1", "-movflags", "+faststart", "-f", "mp4", str(out)]
    await run_command(
        ffmpeg_args(ffmpeg, args), duration=info.duration, on_progress=on_progress, handle=handle
    )


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
    if info.remuxable_to_mp4 and not info.is_hdr:
        args += ["-c", "copy"]
    else:
        args += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k",
        ]  # fmt: skip
    args += ["-movflags", "+faststart", "-f", "mp4", str(out)]
    args = sdr_commands([args], [(src, info)])[0]
    if encoding:
        return await encoding.run(
            [args], encoder, duration=info.duration, on_progress=on_progress, handle=handle
        )
    await run_command(
        ffmpeg_args(ffmpeg, args), duration=info.duration, on_progress=on_progress, handle=handle
    )
    return None
