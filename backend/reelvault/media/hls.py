"""On-demand, aligned H.264/AAC VOD renditions; generated paths are always local."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .ffmpeg import ProcessHandle, ProgressCallback, ffmpeg_args, run_command
from .probe import MediaInfo, probe


def ladder(info: MediaInfo) -> list[tuple[int, int]]:
    if info.duration <= 0 or info.height < 2 or info.width < 2:
        raise ValueError("视频尺寸或时长无效")
    heights = [h for h in (360, 720, 1080) if h <= info.height]
    if not heights:
        heights = [max(2, info.height // 2 * 2)]
    return [(h, 800_000 if h <= 360 else 2_500_000 if h <= 720 else 5_000_000) for h in heights]


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
) -> list[dict[str, Any]]:
    out.mkdir(parents=True, exist_ok=True)
    variants = ladder(info)
    fps = min(30, max(1, info.fps or 25))
    renditions = []
    for index, (height, bitrate) in enumerate(variants):
        folder = out / f"v{index}"
        folder.mkdir()
        graph = (
            f"[0:{info.video_index}]setpts=PTS-STARTPTS,"
            f"tpad=start_mode=clone:start_duration={max(0, info.video_delay):.9f}:"
            f"stop_mode=clone:stop_duration={info.duration:.9f},fps={fps:.9f},"
            f"trim=duration={info.duration:.9f},setpts=PTS-STARTPTS,"
            f"scale=w='max(2,trunc(min(1920,{height}*dar)/2)*2)':"
            f"h='max(2,trunc(min({height},1920/dar)/2)*2)',setsar=1,format=yuv420p[v]"
        )
        args = ["-i", str(source.resolve())]
        if info.has_audio:
            graph += (
                f";[0:{info.audio_index}]asetpts=PTS-STARTPTS,aresample=48000,"
                f"adelay=delays={max(0, info.audio_delay) * 1000:.6f}:all=1,apad,"
                f"atrim=duration={info.duration:.9f}[a]"
            )
        args += [
            "-filter_complex",
            graph,
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-profile:v",
            "main",
            "-level:v",
            "4.0",
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
            args += ["-map", "[a]", "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000"]
        else:
            args += ["-an"]
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
            "seg_%06d.ts",
            "index.m3u8",
        ]

        def progress(fraction: float, step: int = index) -> None:
            on_progress((step + fraction) / len(variants))

        await run_command(
            ffmpeg_args(ffmpeg, args),
            cwd=folder,
            duration=info.duration,
            handle=handle,
            on_progress=progress,
        )
        encoded = await probe(ffprobe, str(folder / "index.m3u8"), handle)
        files = list(folder.glob("*.ts"))
        if not files:
            raise RuntimeError("HLS 没有生成分片")
        renditions.append(
            {
                "name": f"v{index}",
                "height": encoded.height,
                "width": encoded.width,
                "bitrate": bitrate + (128_000 if info.has_audio else 0),
                "fps": fps,
            }
        )
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-INDEPENDENT-SEGMENTS"]
    for rendition in renditions:
        lines += [
            f"#EXT-X-STREAM-INF:BANDWIDTH={rendition['bitrate']},RESOLUTION={rendition['width']}x{rendition['height']},FRAME-RATE={fps:.3f}",
            f"{rendition['name']}/index.m3u8",
        ]
    (out / "master.m3u8").write_text("\n".join(lines) + "\n")
    return renditions
