"""Frame-aligned local effects with bounded reverse buffering and lossless staging."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .ops import MP4_FLAGS, EffectParams, OpError, OpPlan, Source, aac, atempo_chain, x264

# Bound frame storage in each reverse filter. Decoders/encoders need additional memory.
REVERSE_BUFFER_BYTES = 128 * 1024 * 1024


def plan_effect(
    p: EffectParams,
    src: Source,
    out: Path,
    tmp: Path,
) -> tuple[OpPlan, dict[str, Any]]:
    path, info = src
    path, out, tmp = path.resolve(), out.resolve(), tmp.resolve()
    if p.start >= info.duration:
        raise OpError("效果位置超出视频时长")
    fps = info.fps or 30
    frames = max(1, math.ceil(info.duration * fps - 1e-6))
    first = min(frames - 1, math.floor(p.start * fps + 0.5))
    last = min(frames, math.floor(min(p.end or info.duration, info.duration) * fps + 0.5))
    if p.mode != "freeze" and last <= first:
        raise OpError("效果片段至少需要一帧")
    total = frames / fps
    normalized = tmp / "source.mkv"
    graph = [
        f"[0:{info.video_index}]setpts=PTS-STARTPTS,"
        f"tpad=start_mode=clone:start_duration={max(0, info.video_delay):.9f}:"
        f"stop_mode=clone:stop_duration={total:.9f},"
        f"fps={fps:.9f},trim=end_frame={frames},setpts=PTS-STARTPTS,"
        "pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p[v]"
    ]
    maps = ["-map", "[v]"]
    audio = info.has_audio
    if audio:
        graph.append(
            f"[0:{info.audio_index}]aresample=48000,aformat=channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS,adelay={max(0, round(info.audio_delay * 48000))}S:all=1,"
            f"apad,atrim=duration={total:.9f},asetpts=PTS-STARTPTS[a]"
        )
        maps += ["-map", "[a]"]
    lossless = ["-c:v", "ffv1", "-level", "3", "-g", "1", "-pix_fmt", "yuv420p"]
    if audio:
        lossless += ["-c:a", "pcm_s16le"]
    commands = [
        [
            "-i",
            str(path),
            "-filter_complex",
            ";".join(graph),
            *maps,
            *lossless,
            "-t",
            f"{total:.9f}",
            str(normalized),
        ]
    ]
    pieces: list[Path] = []
    piece_durations: list[float] = []

    def piece(start: int, count: int, mode: str = "normal") -> float:
        if count <= 0:
            return 0
        dest = tmp / f"piece-{len(pieces):06d}.mkv"
        pieces.append(dest)
        input_duration = count / fps
        duration = input_duration
        vf = f"trim=end_frame={count},setpts=PTS-STARTPTS"
        # Seek a fraction of a frame early: Matroska millisecond timestamps must
        # not drop the intended first frame at fractional frame rates.
        seek = max(0, (start - 0.25) / fps)
        audio_offset = start / fps - seek
        af = f"atrim=start={audio_offset:.9f}:duration={duration:.9f},asetpts=PTS-STARTPTS"
        args = ["-ss", f"{seek:.9f}", "-i", str(normalized)]
        if mode == "reverse":
            vf += ",reverse,setpts=PTS-STARTPTS"
            af += ",areverse,asetpts=PTS-STARTPTS"
        elif mode == "slow":
            count_out = max(1, math.floor(count / p.factor + 0.5))
            duration = count_out / fps
            vf += f",setpts=PTS/{p.factor:.9f},fps={fps:.9f}"
            af += f",{atempo_chain(p.factor)}"
        elif mode == "freeze":
            duration = max(1, math.floor(p.duration * fps + 0.5)) / fps
            vf = (
                f"trim=end_frame=1,setpts=PTS-STARTPTS,"
                f"tpad=stop_mode=clone:stop_duration={duration:.9f}"
            )
            af = f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:.9f}"
        vf += f",tpad=stop_mode=clone:stop_duration={duration:.9f},trim=duration={duration:.9f}"
        graph = [f"[0:v]{vf}[v]"]
        maps = ["-map", "[v]"]
        if audio:
            # Freeze adds silent time; normal playback resumes from the same source point.
            audio_input = "" if mode == "freeze" else "[0:a]"
            graph.append(
                f"{audio_input}{af},apad,atrim=duration={duration:.9f},asetpts=PTS-STARTPTS[a]"
            )
            maps += ["-map", "[a]"]
        args += [
            "-filter_complex",
            ";".join(graph),
            *maps,
            *lossless,
            "-t",
            f"{duration:.9f}",
            str(dest),
        ]
        commands.append(args)
        piece_durations.append(duration)
        return duration

    before = piece(0, first)
    if p.mode == "freeze":
        effect_duration = piece(first, 1, "freeze")
        after = piece(first, frames - first)
    elif p.mode == "slow":
        effect_duration = piece(first, last - first, "slow")
        after = piece(last, frames - last)
    else:
        bytes_per_frame = math.ceil(info.width / 2) * 2 * math.ceil(info.height / 2) * 2 * 1.5 + (
            192000 / fps if audio else 0
        )
        chunk = max(1, min(round(fps * 5), int(REVERSE_BUFFER_BYTES / bytes_per_frame)))
        # Traverse source chunks from the end, reverse each chunk, then stream-concat them.
        end = last
        effect_duration = 0
        while end > first:
            begin = max(first, end - chunk)
            effect_duration += piece(begin, end - begin, "reverse")
            end = begin
        after = piece(last, frames - last)
    manifest = tmp / "pieces.txt"
    manifest.write_text(
        "".join(
            f"file '{path.name}'\nduration {duration:.9f}\n"
            for path, duration in zip(pieces, piece_durations, strict=True)
        ),
        encoding="utf-8",
    )
    duration = before + effect_duration + after
    commands.append(
        [
            "-f",
            "concat",
            "-safe",
            "1",
            "-i",
            manifest.name,
            "-map",
            "0:v:0",
            *(["-map", "0:a:0", *aac()] if audio else []),
            *x264(p.crf),
            "-t",
            f"{duration:.9f}",
            *MP4_FLAGS,
            str(out),
        ]
    )
    actual = {
        "start": first / fps,
        "end": (first if p.mode == "freeze" else last) / fps,
        "effect_duration": effect_duration,
        "output_duration": duration,
        "fps": fps,
    }
    return OpPlan(commands, duration, cwd=tmp), actual
