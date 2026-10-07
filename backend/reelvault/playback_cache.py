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


def needs_copy(video: Video) -> bool:
    return not MediaInfo(
        container=video.container or "",
        video_codec=video.video_codec or "",
        audio_codec=video.audio_codec,
    ).browser_playable


def estimate(video: Video) -> int:
    rate = max(video.bitrate, video.width * video.height * (video.fps or 30) * 0.5) + 256_000
    return math.ceil(max(0, video.duration) * rate / 8 * 1.5) + 32 * MIB


def cached_path(settings: Settings, video: Video) -> Path | None:
    if not video.playable_path:
        return None
    path = abs_path(settings, video.playable_path)
    if not path.is_file():
        return None
    signature = (video.meta or {}).get("playable_signature")
    # Existing installations have unversioned eager copies; retain those caches.
    if signature is not None:
        try:
            if signature != source_signature(abs_path(settings, video.file_path)):
                return None
        except OSError:
            return None
    return path


def stream_path(settings: Settings, video: Video) -> Path:
    copy = cached_path(settings, video)
    if copy is not None:
        return copy
    if needs_copy(video):
        raise APIError(409, "请先在视频详情中生成兼容播放缓存", code="playback_cache_required")
    return abs_path(settings, video.file_path)


def remove_copy(settings: Settings, video_id: str, path: Path | None) -> None:
    # Never remove originals or files outside this video's derived directory.
    root = (settings.derived_dir / video_id).resolve()
    if path is not None and path.resolve().is_relative_to(root):
        path.unlink(missing_ok=True)
