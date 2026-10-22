"""On-demand, aligned H.264/AAC VOD renditions; generated paths are always local."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .color import SDR_TAGS, source_filter
from .encoding import EncoderRuntime
from .ffmpeg import ProcessHandle, ProgressCallback, ffmpeg_args, run_command
from .probe import MediaInfo, probe

# Short side of each rung and its video bitrate at up to 30 fps.
RUNGS = ((360, 800_000), (720, 2_500_000), (1080, 5_000_000), (1440, 9_000_000), (2160, 16_000_000))
MAX_FPS = 60
# H.264 Table A-1: (level, max macroblocks per frame, max macroblocks per second).
LEVELS = (
    ("4.0", 8192, 245760),
    ("4.2", 8704, 522240),
    ("5.0", 22080, 589824),
    ("5.1", 36864, 983040),
    ("5.2", 36864, 2073600),
)


def output_fps(info: MediaInfo) -> float:
    return min(MAX_FPS, max(1, info.fps or 25))


def long_side(short: int) -> int:
    """Bounding-box long side; 1920 up to 1080p as before, 16:9 above."""
    return max(1920, math.ceil(short * 16 / 9))


def h264_level(short: int, fps: float) -> str:
    # Upper bound: each rendition fits within short x long_side(short).
    macroblocks = math.ceil(long_side(short) / 16) * math.ceil(short / 16)
    return next(
        (
            name
            for name, frame, rate in LEVELS
            if macroblocks <= frame and macroblocks * fps <= rate
        ),
        LEVELS[-1][0],
    )


def ladder(info: MediaInfo) -> list[tuple[int, int]]:
    """(short side, video bitrate) per rendition; portrait videos rank by width."""
    if info.duration <= 0 or info.height < 2 or info.width < 2:
        raise ValueError("视频尺寸或时长无效")
    short = min(info.width, info.height)
    sides = [side for side, _ in RUNGS if side <= short] or [max(2, short // 2 * 2)]
    source = info.video_bitrate or info.bitrate
    result = []
    for side in sides:
        rate = next(rate for rung, rate in RUNGS if rung >= side)
        if output_fps(info) > 30:
            rate = rate * 3 // 2
        if source > 0:
            # Spending more than the source has only inflates the cache.
            rate = min(rate, max(300_000, source))
        result.append((side, rate))
    return result


def estimated_bytes(info: MediaInfo) -> int:
    return math.ceil(
        sum(rate + (128_000 if info.has_audio else 0) for _, rate in ladder(info))
        * info.duration
        / 8
        * 1.3
    )


async def generate(
    ffmpeg: str,
    ffprobe: str,
    source: Path,
    info: MediaInfo,
    out: Path,
    *,
    handle: ProcessHandle,
    on_progress: ProgressCallback,
    encoding: EncoderRuntime | None = None,
    encoder: str = "software",
) -> list[dict[str, Any]]:
    out.mkdir(parents=True, exist_ok=True)
    variants = ladder(info)
    fps = output_fps(info)
    count = len(variants)
    tone = source_filter(info)
    # Decode and normalise timing once, then split into every rendition in one process.
    graph = (
        f"[0:{info.video_index}]" + (tone + "," if tone else "") + "setpts=PTS-STARTPTS,"
        f"tpad=start_mode=clone:start_duration={max(0, info.video_delay):.9f}:"
        f"stop_mode=clone:stop_duration={info.duration:.9f},fps={fps:.9f},"
        f"trim=duration={info.duration:.9f},setpts=PTS-STARTPTS,"
        f"split={count}" + "".join(f"[s{i}]" for i in range(count))
    )
    for index, (short, _) in enumerate(variants):
        long = long_side(short)
        # Fit the display aspect ratio within short x long, portrait or landscape.
        graph += (
            f";[s{index}]scale="
            f"w='max(2,trunc(if(gte(dar,1),min({long},{short}*dar),min({short},{long}*dar))/2)*2)':"
            f"h='max(2,trunc(if(gte(dar,1),min({short},{long}/dar),min({long},{short}/dar))/2)*2)',"
            f"setsar=1,format=yuv420p[v{index}]"
        )
    if info.has_audio:
        graph += (
            f";[0:{info.audio_index}]asetpts=PTS-STARTPTS,aresample=48000,"
            f"adelay=delays={max(0, info.audio_delay) * 1000:.6f}:all=1,apad,"
            f"atrim=duration={info.duration:.9f},asplit={count}"
            + "".join(f"[a{i}]" for i in range(count))
        )
    args = ["-i", str(source.resolve()), "-filter_complex", graph]
    for index, (short, bitrate) in enumerate(variants):
        (out / f"v{index}").mkdir()
        # Map video before audio so hardware adaptation finds each output's video label.
        args += ["-map", f"[v{index}]"]
        if info.has_audio:
            args += ["-map", f"[a{index}]"]
        args += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-profile:v",
            "main",
            "-level:v",
            h264_level(short, fps),
            "-refs",
            "3",
            "-b:v",
            str(bitrate),
            "-maxrate",
            str(bitrate),
            "-bufsize",
            str(bitrate * 2),
            "-g",
            str(math.ceil(fps * 4)),
            "-keyint_min",
            str(math.ceil(fps * 4)),
            "-sc_threshold",
            "0",
            "-flags",
            "+cgop",
            "-force_key_frames",
            "expr:gte(t,n_forced*4)",
        ]
        if info.has_audio:
            args += ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000"]
        else:
            args += ["-an"]
        if info.is_hdr:
            args += SDR_TAGS
        args += [
            "-sn",
            "-dn",
            "-f",
            "hls",
            "-hls_time",
            "4",
            "-hls_playlist_type",
            "vod",
            "-hls_flags",
            "independent_segments+temp_file",
            "-hls_segment_filename",
            f"v{index}/seg_%06d.ts",
            f"v{index}/index.m3u8",
        ]

    if encoding:
        await encoding.run(
            [args],
            encoder,
            duration=info.duration,
            handle=handle,
            on_progress=on_progress,
            cwd=out,
        )
    else:
        await run_command(
            ffmpeg_args(ffmpeg, args),
            cwd=out,
            duration=info.duration,
            handle=handle,
            on_progress=on_progress,
        )
    renditions = []
    for index, (short, bitrate) in enumerate(variants):
        folder = out / f"v{index}"
        encoded = await probe(ffprobe, str(folder / "index.m3u8"), handle)
        if not list(folder.glob("*.ts")):
            raise RuntimeError("HLS 没有生成分片")
        # Main profile (x264 sets constraint_set1) at the rendition's level.
        level = h264_level(short, fps)
        codecs = f"avc1.4D40{round(float(level) * 10):02X}"
        if info.has_audio:
            codecs += ",mp4a.40.2"
        renditions.append(
            {
                "name": f"v{index}",
                "height": encoded.height,
                "width": encoded.width,
                "bitrate": bitrate + (128_000 if info.has_audio else 0),
                "fps": fps,
                "codecs": codecs,
            }
        )
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-INDEPENDENT-SEGMENTS"]
    for rendition in renditions:
        lines += [
            f"#EXT-X-STREAM-INF:BANDWIDTH={rendition['bitrate']},"
            f"RESOLUTION={rendition['width']}x{rendition['height']},"
            f'FRAME-RATE={fps:.3f},CODECS="{rendition["codecs"]}"',
            f"{rendition['name']}/index.m3u8",
        ]
    (out / "master.m3u8").write_text("\n".join(lines) + "\n")
    return renditions
