"""Validate staged edits before publishing them or touching the original."""

from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import BinaryIO

from .ffmpeg import FFmpegError, ProcessHandle, ffmpeg_args, ffprobe_json, run_command


def _webp_image(stream: BinaryIO, tag: bytes, size: int) -> bool:
    prefix = stream.read(min(10, size))
    if tag == b"VP8 ":
        return len(prefix) == 10 and prefix[3:6] == b"\x9d\x01\x2a"
    if tag == b"VP8L":
        return len(prefix) >= 5 and prefix[0] == 0x2F
    return False


def _webp(path: Path, expected_duration: float) -> None:
    # FFmpeg cannot decode animated WebP on every supported build. Inspect its
    # RIFF frame boundaries and timing without reading the whole export into RAM.
    with path.open("rb") as stream:
        header = stream.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WEBP":
            raise FFmpegError("输出校验失败：WebP 文件头无效")
        end = struct.unpack_from("<I", header, 4)[0] + 8
        if end != path.stat().st_size:
            raise FFmpegError("输出校验失败：WebP 文件不完整")
        frames = 0
        duration = 0.0
        image = False
        while stream.tell() < end:
            chunk = stream.read(8)
            if len(chunk) != 8:
                raise FFmpegError("输出校验失败：WebP 数据块不完整")
            size = struct.unpack_from("<I", chunk, 4)[0]
            next_chunk = stream.tell() + size + size % 2
            if next_chunk > end:
                raise FFmpegError("输出校验失败：WebP 数据块越界")
            if chunk[:4] == b"ANMF":
                frame = stream.read(16)
                if size <= 16 or len(frame) != 16:
                    raise FFmpegError("输出校验失败：WebP 帧不完整")
                duration += int.from_bytes(frame[12:15], "little") / 1000
                frames += 1
                frame_end = next_chunk - size % 2
                frame_image = False
                while stream.tell() < frame_end:
                    subchunk = stream.read(8)
                    if len(subchunk) != 8:
                        raise FFmpegError("输出校验失败：WebP 帧数据不完整")
                    subsize = struct.unpack_from("<I", subchunk, 4)[0]
                    subend = stream.tell() + subsize + subsize % 2
                    if subend > frame_end:
                        raise FFmpegError("输出校验失败：WebP 帧数据越界")
                    if subchunk[:4] in {b"VP8 ", b"VP8L"}:
                        frame_image = _webp_image(stream, subchunk[:4], subsize)
                    stream.seek(subend)
                if not frame_image:
                    raise FFmpegError("输出校验失败：WebP 缺少有效帧图像")
            elif chunk[:4] in {b"VP8 ", b"VP8L"}:
                image = _webp_image(stream, chunk[:4], size)
            stream.seek(next_chunk)
        if frames == 0:
            # A one-frame animation may be encoded as an ordinary WebP image.
            if not image or expected_duration > 1:
                raise FFmpegError("输出校验失败：WebP 缺少动画帧")
        elif abs(duration - expected_duration) > max(0.25, expected_duration * 0.02):
            raise FFmpegError("输出校验失败：WebP 动画时长与预期不符")


async def validate_output(
    ffmpeg: str,
    ffprobe: str,
    path: Path,
    *,
    expected_duration: float,
    video: bool = True,
    audio: bool = False,
    duration_tolerance: float = 1.0,
    handle: ProcessHandle | None = None,
) -> None:
    """Check tracks/timing and decode boundary samples, without a second full encode.

    This is a bounded sanity check, not a guarantee that every packet decodes.
    """
    if not path.is_file() or path.stat().st_size == 0:
        raise FFmpegError("输出校验失败：未生成有效文件")
    if path.suffix.lower() == ".webp":
        _webp(path, expected_duration)
        return
    data = await ffprobe_json(ffprobe, str(path), handle)
    streams = [
        s for s in data.get("streams", []) if not (s.get("disposition") or {}).get("attached_pic")
    ]
    videos = [s for s in streams if s.get("codec_type") == "video"]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if video and not videos:
        raise FFmpegError("输出校验失败：缺少视频轨道")
    if audio and not audios:
        raise FFmpegError("输出校验失败：缺少音频轨道")
    if video and any(
        int(s.get("width") or 0) <= 0 or int(s.get("height") or 0) <= 0 for s in videos
    ):
        raise FFmpegError("输出校验失败：视频尺寸无效")
    fmt = data.get("format") or {}
    duration = float(fmt.get("duration") or 0)
    if not duration:
        duration = max((float(s.get("duration") or 0) for s in streams), default=0)
    if not math.isfinite(duration) or duration <= 0:
        raise FFmpegError("输出校验失败：无法读取有效时长")
    tolerance = max(duration_tolerance, min(2.0, expected_duration * 0.001))
    if expected_duration > 0 and abs(duration - expected_duration) > tolerance:
        raise FFmpegError(
            f"输出校验失败：预期时长 {expected_duration:.3f} 秒，实际 {duration:.3f} 秒"
        )
    # Explicit stream indices exclude attached covers, subtitles and data streams.
    maps = []
    for stream in [*videos[:1], *audios[:1]]:
        maps += ["-map", f"0:{stream['index']}"]
    # MP4/Matroska duration can include a positive timestamp offset. Input-side
    # -ss is relative to start_time, so remove that offset when seeking the tail.
    seek_duration = duration
    if set(fmt.get("format_name", "").split(",")) & {"mov", "mp4", "matroska", "webm"}:
        seek_duration = max(0.0, duration - max(0.0, float(fmt.get("start_time") or 0)))
    for start in dict.fromkeys((0.0, max(0.0, seek_duration - 2))):
        args = [
            "-xerror",
            "-err_detect",
            "explode",
            "-ss",
            f"{start:.6f}",
            "-i",
            str(path),
            *maps,
            "-t",
            "2",
            "-f",
            "null",
            "-",
        ]
        decoded = await run_command(ffmpeg_args(ffmpeg, args), handle=handle, timeout=60)
        progress = decoded.extra.get("progress", {})
        if video and progress.get("frame", 0) < 1:
            raise FFmpegError("输出校验失败：采样位置无法解码视频帧")
        if not video and progress.get("out_time_us", 0) <= 0:
            raise FFmpegError("输出校验失败：采样位置无法解码音频")
