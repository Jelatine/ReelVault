from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from .ffmpeg import FFmpegError, ffmpeg_args, run_command
from .ops import MP4_FLAGS, OpError, OpPlan, Source, SubtitleParams, audio_for_mp4, maps, x264

FORMATS = {".srt": "srt", ".ass": "ass", ".vtt": "webvtt"}


async def to_vtt(ffmpeg: str, path: Path, stream_index: int | None = None) -> bytes:
    args = ["-i", str(path)]
    if stream_index is None:
        args = ["-f", FORMATS[path.suffix.lower()], *args]
    args += [
        "-map",
        f"0:{stream_index}" if stream_index is not None else "0:s:0",
        "-c:s",
        "webvtt",
        "-f",
        "webvtt",
        "pipe:1",
    ]
    result = await asyncio.wait_for(run_command(ffmpeg_args(ffmpeg, args, progress=False)), 30)
    if b" --> " not in result.stdout:
        raise FFmpegError("字幕没有有效的时间片段")
    return result.stdout


def plan_subtitle(
    p: SubtitleParams, src: Source, out: Path, tmp: Path, asset: Path | None = None
) -> OpPlan:
    path, info = src
    path, out, tmp = path.resolve(), out.resolve(), tmp.resolve()
    commands = []
    if asset:
        name = "subtitle" + asset.suffix.lower()
        shutil.copyfile(asset, tmp / name)
    else:
        stream = next(
            (s for s in info.extra.get("subtitle_streams", []) if s["index"] == p.embedded_index),
            None,
        )
        if stream is None:
            raise OpError("内封字幕轨道不存在")
        if not stream["text"]:
            graph = (
                f"[0:{stream['index']}]scale={info.width}:{info.height},setsar=1[sub];"
                f"[0:{info.video_index}][sub]overlay=eof_action=pass:shortest=0[v]"
            )
            args = ["-i", str(path), "-filter_complex", graph, "-map", "[v]"]
            if info.audio_index is not None:
                args += ["-map", f"0:{info.audio_index}"]
            args += [*x264(p.crf), *audio_for_mp4(info), *MP4_FLAGS, str(out)]
            return OpPlan([args], info.duration)
        name = "subtitle.ass"
        commands.append(
            ["-i", str(path), "-map", f"0:{stream['index']}", "-c:s", "ass", str(tmp / name)]
        )
    args = [
        "-i",
        str(path),
        *maps(info),
        "-vf",
        f"subtitles=filename={name}",
        *x264(p.crf),
        *audio_for_mp4(info),
        *MP4_FLAGS,
        str(out),
    ]
    return OpPlan([*commands, args], info.duration, cwd=tmp)
