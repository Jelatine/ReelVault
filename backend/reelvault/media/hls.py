"""On-demand, aligned H.264/AAC VOD renditions; generated paths are always local."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .color import SDR_TAGS, needs_tone_mapping, source_filter, tone_device_args
from .encoding import EncoderRuntime
from .ffmpeg import ProcessHandle, ProgressCallback, ffmpeg_args, run_command
from .probe import MediaInfo, probe

# Short side of each rung and its video bitrate at up to 30 fps.
RUNGS = ((360, 800_000), (720, 2_500_000), (1080, 5_000_000), (1440, 9_000_000), (2160, 16_000_000))
MAX_FPS = 60
# Longest source keyframe interval a copied rendition accepts: segments start at the
# first keyframe after each 4 s boundary, so longer GOPs make overlong segments.
COPY_MAX_GOP = 10.0
H264_PROFILES = {"Constrained Baseline": "42E0", "Baseline": "4200", "Main": "4D40", "High": "6400"}
HEVC_PROFILES = {"Main": "1.6", "Main 10": "2.4"}
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


def copy_codecs(info: MediaInfo, keyframes: list[float] | None) -> str | None:
    """RFC 6381 codecs of a rendition that segments the source video without re-encoding.

    None when the source cannot be copied: its timeline, rate or format would not line
    up with the encoded renditions, its GOPs are too long, or browsers cannot play it.
    """
    if not keyframes:
        return None
    gaps = [b - a for a, b in zip([0.0, *keyframes], [*keyframes, info.duration], strict=True)]
    return source_codecs(info) if max(gaps) <= COPY_MAX_GOP else None


def source_codecs(info: MediaInfo) -> str | None:
    if info.is_hdr or info.dolby_vision_only or info.interlaced or info.variable_frame_rate:
        return None
    if not info.video_level:
        return None
    if info.rotation or abs(info.video_delay) > 0.002 or not 0 < info.fps <= MAX_FPS:
        return None
    level = info.video_level
    if info.video_codec == "h264" and info.pix_fmt in {"yuv420p", "yuvj420p"}:
        profile = H264_PROFILES.get(info.video_profile)
        return f"avc1.{profile}{level:02X}" if profile else None
    if info.video_codec == "hevc" and info.video_profile in HEVC_PROFILES:
        return f"hvc1.{HEVC_PROFILES[info.video_profile]}.L{level}.B0"
    if info.video_codec == "av1" and info.video_profile == "Main":
        return f"av01.0.{level:02d}M.{10 if info.high_bit_depth else 8:02d}"
    return None


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
    # A copied source rendition may be added; keyframe spacing is only known later.
    copied = (info.video_bitrate or info.bitrate) if source_codecs(info) else 0
    return math.ceil(
        (sum(rate + (128_000 if info.has_audio else 0) for _, rate in ladder(info)) + copied)
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
    keyframes: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Encode the ladder; with ``keyframes``, also copy the source video when possible.

    A copied rendition cuts segments at source keyframes, so the encoded renditions then
    place their keyframes at the source keyframes too and every rendition switches at
    the same times. H.264 copies replace the encoded rung of the same size; HEVC and AV1
    copies (in fMP4) are an extra tier for browsers that decode them.
    """
    out.mkdir(parents=True, exist_ok=True)
    variants = ladder(info)
    fps = output_fps(info)
    copied = copy_codecs(info, keyframes)
    short_side = min(info.width, info.height)
    if copied and info.video_codec == "h264":
        variants = [variant for variant in variants if variant[0] != short_side]
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
    audio_chain = (
        f"asetpts=PTS-STARTPTS,aresample=48000,"
        f"adelay=delays={max(0, info.audio_delay) * 1000:.6f}:all=1,apad,"
        f"atrim=duration={info.duration:.9f}"
    )
    if info.has_audio:
        graph += f";[0:{info.audio_index}]{audio_chain},asplit={count}" + "".join(
            f"[a{i}]" for i in range(count)
        )
    device = tone_device_args() if needs_tone_mapping(info) else []
    args = [*device, "-i", str(source.resolve()), "-filter_complex", graph]
    if copied:
        # Source keyframes are at most COPY_MAX_GOP apart: no keyframes in between.
        gop = str(math.ceil(fps * (COPY_MAX_GOP + 1)))
        keys = ["-g", gop, "-keyint_min", "1", "-force_key_frames", "source"]
    else:
        gop = str(math.ceil(fps * 4))
        keys = ["-g", gop, "-keyint_min", gop, "-force_key_frames", "expr:gte(t,n_forced*4)"]
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
            *keys[:4],
            "-sc_threshold",
            "0",
            "-flags",
            "+cgop",
            *keys[4:],
        ]
        if info.has_audio:
            args += ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000"]
        else:
            args += ["-an"]
        if needs_tone_mapping(info):
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

    commands = [args] if variants else []
    if copied:
        name = f"v{count}"
        (out / name).mkdir()
        fmp4 = info.video_codec != "h264"
        copy = ["-i", str(source.resolve()), "-map", f"0:{info.video_index}"]
        if info.has_audio:
            copy += ["-map", f"0:{info.audio_index}", "-af", audio_chain]
            copy += ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000"]
        else:
            copy += ["-an"]
        copy += ["-c:v", "copy", "-sn", "-dn"]
        if info.video_codec == "hevc":
            copy += ["-tag:v", "hvc1"]
        copy += ["-f", "hls", "-hls_time", "4", "-hls_playlist_type", "vod"]
        copy += ["-hls_flags", "independent_segments+temp_file"]
        if fmp4:
            copy += ["-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4"]
        segment = "seg_%06d.m4s" if fmp4 else "seg_%06d.ts"
        copy += ["-hls_segment_filename", f"{name}/{segment}", f"{name}/index.m3u8"]
        commands.append(copy)
    if encoding:
        await encoding.run(
            commands,
            encoder,
            duration=info.duration,
            handle=handle,
            on_progress=on_progress,
            cwd=out,
        )
    else:
        for index, command in enumerate(commands):

            def progress(value: float, index: int = index) -> None:
                on_progress((index + value) / len(commands))

            await run_command(
                ffmpeg_args(ffmpeg, command),
                cwd=out,
                duration=info.duration,
                handle=handle,
                on_progress=progress,
            )
    renditions = []
    for index, (short, bitrate) in enumerate(variants):
        folder = out / f"v{index}"
        encoded = await probe(ffprobe, str(folder / "index.m3u8"), handle)
        if not list(folder.glob("seg_*")):
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
    if copied:
        folder = out / f"v{count}"
        if not list(folder.glob("seg_*")):
            raise RuntimeError("HLS 没有生成分片")
        copy_info = await probe(ffprobe, str(folder / "index.m3u8"), handle)
        rate = info.video_bitrate or info.bitrate or (variants[-1][1] if variants else 300_000)
        renditions.append(
            {
                "name": f"v{count}",
                "height": copy_info.height,
                "width": copy_info.width,
                "bitrate": rate + (128_000 if info.has_audio else 0),
                "fps": fps,
                "codecs": copied + (",mp4a.40.2" if info.has_audio else ""),
                "copy": True,
            }
        )
    # Version 7 is needed for fMP4 (EXT-X-MAP) renditions.
    version = 7 if copied and info.video_codec != "h264" else 3
    lines = ["#EXTM3U", f"#EXT-X-VERSION:{version}", "#EXT-X-INDEPENDENT-SEGMENTS"]
    for rendition in renditions:
        lines += [
            f"#EXT-X-STREAM-INF:BANDWIDTH={rendition['bitrate']},"
            f"RESOLUTION={rendition['width']}x{rendition['height']},"
            f'FRAME-RATE={rendition["fps"]:.3f},CODECS="{rendition["codecs"]}"',
            f"{rendition['name']}/index.m3u8",
        ]
    (out / "master.m3u8").write_text("\n".join(lines) + "\n")
    return renditions
