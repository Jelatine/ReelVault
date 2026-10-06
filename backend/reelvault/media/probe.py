from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .ffmpeg import FFmpegError, ProcessHandle, ffprobe_json

BROWSER_CONTAINERS = {"mp4", "mov", "webm", "m4v"}
BROWSER_VIDEO_CODECS = {"h264", "vp8", "vp9", "av1"}
BROWSER_AUDIO_CODECS = {"aac", "mp3", "opus", "vorbis"}
MP4_AUDIO_CODECS = {"aac", "mp3", "opus", "alac", "ac3"}


@dataclass
class MediaInfo:
    duration: float = 0
    size: int = 0
    bitrate: int = 0
    container: str = ""
    # Coded size of the video stream.
    coded_width: int = 0
    coded_height: int = 0
    # Size after applying the rotation metadata (what the viewer sees).
    width: int = 0
    height: int = 0
    rotation: int = 0
    fps: float = 0
    video_codec: str = ""
    video_index: int = 0
    pix_fmt: str = ""
    audio_codec: str | None = None
    audio_index: int | None = None
    sample_rate: int = 0
    channels: int = 0
    audio_delay: float = 0
    video_delay: float = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def has_audio(self) -> bool:
        return self.audio_codec is not None

    @property
    def browser_playable(self) -> bool:
        return (
            self.container in BROWSER_CONTAINERS
            and self.video_codec in BROWSER_VIDEO_CODECS
            and (self.audio_codec is None or self.audio_codec in BROWSER_AUDIO_CODECS)
        )

    @property
    def remuxable_to_mp4(self) -> bool:
        """Codecs are fine for the browser; only the container is the problem."""
        return self.video_codec in {"h264", "av1"} and (
            self.audio_codec is None or self.audio_codec in {"aac", "mp3", "opus"}
        )

    def to_meta(self) -> dict[str, Any]:
        return {
            **self.extra,
            "rotation": self.rotation,
            "coded_width": self.coded_width,
            "coded_height": self.coded_height,
            "video_index": self.video_index,
            "audio_index": self.audio_index,
            "pix_fmt": self.pix_fmt,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "audio_delay": self.audio_delay,
        }


def _parse_rate(rate: str | None) -> float:
    if not rate or rate == "0/0":
        return 0.0
    if "/" in rate:
        num, den = rate.split("/", 1)
        try:
            return float(num) / float(den) if float(den) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(rate)
    except ValueError:
        return 0.0


def _rotation(stream: dict[str, Any]) -> int:
    rot: float = 0
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            rot = float(sd["rotation"])
            break
    else:
        tag = (stream.get("tags") or {}).get("rotate")
        if tag:
            # The legacy tag is clockwise; display-matrix rotation is counter-clockwise.
            rot = -float(tag)
    return int(round(rot)) % 360


def _container(format_name: str, path: str) -> str:
    names = format_name.split(",")
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if "mp4" in names or "mov" in names:
        return ext if ext in {"mp4", "mov", "m4v"} else "mp4"
    if "matroska" in names or "webm" in names:
        return "webm" if ext == "webm" else "mkv"
    return names[0] if names else ext


def parse_probe(data: dict[str, Any], path: str = "") -> MediaInfo:
    fmt = data.get("format") or {}
    info = MediaInfo()
    info.duration = float(fmt.get("duration") or 0)
    info.size = int(fmt.get("size") or 0)
    info.bitrate = int(fmt.get("bit_rate") or 0)
    info.container = _container(fmt.get("format_name", ""), path)

    video = None
    audio = None
    for s in data.get("streams") or []:
        kind = s.get("codec_type")
        if kind == "video" and video is None:
            if (s.get("disposition") or {}).get("attached_pic"):
                continue
            video = s
        elif kind == "audio" and audio is None:
            audio = s
    if video is None:
        raise FFmpegError("文件中没有视频流")

    info.video_index = int(video.get("index", 0))
    info.video_delay = float(video.get("start_time") or 0) - float(fmt.get("start_time") or 0)
    info.extra = {**(fmt.get("tags") or {}), **(video.get("tags") or {})}
    info.extra["subtitle_streams"] = subtitle_streams(data)
    info.video_codec = video.get("codec_name", "")
    info.pix_fmt = video.get("pix_fmt", "")
    info.coded_width = int(video.get("width") or 0)
    info.coded_height = int(video.get("height") or 0)
    info.rotation = _rotation(video)
    if info.rotation in (90, 270):
        info.width, info.height = info.coded_height, info.coded_width
    else:
        info.width, info.height = info.coded_width, info.coded_height
    info.fps = round(
        _parse_rate(video.get("avg_frame_rate")) or _parse_rate(video.get("r_frame_rate")), 3
    )
    if not info.duration:
        info.duration = float(video.get("duration") or 0)

    if audio is not None:
        info.audio_codec = audio.get("codec_name") or "unknown"
        info.audio_index = int(audio.get("index", 0))
        info.sample_rate = int(audio.get("sample_rate") or 0)
        info.channels = int(audio.get("channels") or 0)
        info.audio_delay = float(audio.get("start_time") or 0) - float(fmt.get("start_time") or 0)
    return info


async def probe(ffprobe: str, path: str, handle: ProcessHandle | None = None) -> MediaInfo:
    return parse_probe(await ffprobe_json(ffprobe, path, handle), path)


def subtitle_streams(data: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for stream in data.get("streams", []):
        if stream.get("codec_type") != "subtitle":
            continue
        tags = stream.get("tags") or {}
        codec = stream.get("codec_name", "")
        result.append(
            {
                "index": int(stream["index"]),
                "subtitle_index": len(result),
                "codec": codec,
                "language": tags.get("language", "und"),
                "label": tags.get("title") or f"内封字幕 {len(result) + 1}",
                "text": codec in {"subrip", "ass", "ssa", "webvtt", "mov_text", "text"},
            }
        )
    return result
