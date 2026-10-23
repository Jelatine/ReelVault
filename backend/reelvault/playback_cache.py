"""Source-specific browser playback copies; the original always remains intact."""

from __future__ import annotations

import math
from pathlib import Path

from .config import Settings
from .errors import APIError
from .library import abs_path
from .media.probe import MediaInfo
from .media.scenes import source_signature
from .models import Video
from .storage import MIB

# HEVC-capable browsers get the original HEVC stream, remuxed into MP4 when needed,
# instead of an H.264 transcode. Clients ask for it with ?codec=hevc.
HEVC_PATH, HEVC_SIGNATURE = "hevc_playable_path", "hevc_playable_signature"


def media_info(video: Video) -> MediaInfo:
    meta = video.meta or {}
    return MediaInfo(
        container=video.container or "",
        video_codec=video.video_codec or "",
        video_tag=meta.get("video_tag", ""),
        audio_codec=video.audio_codec,
        color_transfer=meta.get("color_transfer", ""),
    )


def hevc_variant(video: Video, hevc: bool) -> bool:
    """Whether a client that plays HEVC is served HEVC rather than the H.264 copy."""
    return hevc and media_info(video).hevc_remuxable


def needs_copy(video: Video, hevc: bool = False) -> bool:
    if hevc_variant(video, hevc):
        return not media_info(video).hevc_direct
    return not media_info(video).browser_playable


def estimate(video: Video, hevc: bool = False) -> int:
    if hevc_variant(video, hevc):
        return video.size + 32 * MIB
    rate = max(video.bitrate, video.width * video.height * (video.fps or 30) * 0.5) + 256_000
    return math.ceil(max(0, video.duration) * rate / 8 * 1.5) + 32 * MIB


def cached_path(settings: Settings, video: Video, hevc: bool = False) -> Path | None:
    meta = video.meta or {}
    variant = hevc_variant(video, hevc)
    relative = meta.get(HEVC_PATH) if variant else video.playable_path
    if not relative:
        return None
    path = abs_path(settings, relative)
    if not path.is_file():
        return None
    signature = meta.get(HEVC_SIGNATURE if variant else "playable_signature")
    # Existing installations have unversioned eager copies; retain those caches.
    if signature is not None:
        try:
            if signature != source_signature(abs_path(settings, video.file_path)):
                return None
        except OSError:
            return None
    return path


def stream_path(settings: Settings, video: Video, hevc: bool = False) -> Path:
    copy = cached_path(settings, video, hevc)
    if copy is not None:
        return copy
    if needs_copy(video, hevc):
        raise APIError(409, "请先在视频详情中生成兼容播放缓存", code="playback_cache_required")
    return abs_path(settings, video.file_path)


def remove_copy(settings: Settings, video_id: str, path: Path | None) -> None:
    # Never remove originals or files outside this video's derived directory.
    root = (settings.derived_dir / video_id).resolve()
    if path is not None and path.resolve().is_relative_to(root):
        path.unlink(missing_ok=True)
