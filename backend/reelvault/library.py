"""Library helpers shared by the API and the job handlers."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .config import Settings
from .media.probe import MediaInfo
from .models import Folder, Video, new_id

VIDEO_EXTENSIONS = {
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".ts", ".m2ts",
    ".mts", ".mpg", ".mpeg", ".3gp", ".ogv",
}  # fmt: skip


def abs_path(settings: Settings, rel: str) -> Path:
    return settings.data_dir / rel


def rel_path(settings: Settings, path: Path) -> str:
    return str(path.relative_to(settings.data_dir))


def derived_dir(settings: Settings, video_id: str) -> Path:
    d = settings.derived_dir / video_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def ext_of(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return suffix if suffix in VIDEO_EXTENSIONS else ".mp4"


def stem_of(name: str) -> str:
    return Path(name).stem or "video"


def store_file(
    db: Session,
    settings: Settings,
    src: Path,
    *,
    title: str,
    original_name: str,
    folder_id: int | None,
    move: bool = True,
    source_path: str | None = None,
    video_id: str | None = None,
    commit: bool = True,
) -> Video:
    """Place a file into the library and create its (processing) Video row."""
    vid = video_id or new_id()
    dest = settings.library_dir / f"{vid}{ext_of(original_name)}"
    if move:
        shutil.move(str(src), dest)
    else:
        try:
            dest.hardlink_to(src)
        except OSError:
            shutil.copy2(src, dest)
    video = Video(
        id=vid,
        title=title[:255],
        original_name=original_name[:255],
        file_path=rel_path(settings, dest),
        folder_id=folder_id,
        size=dest.stat().st_size,
        status="processing",
        source_path=source_path,
    )
    try:
        db.add(video)
        if commit:
            db.commit()
        else:
            db.flush()
    except Exception:
        db.rollback()
        if move:
            shutil.move(str(dest), src)
        else:
            dest.unlink(missing_ok=True)
        raise
    return video


def apply_media_info(video: Video, info: MediaInfo, size: int) -> None:
    video.size = size
    video.duration = info.duration
    video.width = info.width
    video.height = info.height
    video.fps = info.fps
    video.bitrate = info.bitrate
    video.container = info.container
    video.video_codec = info.video_codec
    video.audio_codec = info.audio_codec
    video.meta = {**(video.meta or {}), **info.to_meta()}
    captured = info.extra.get("creation_time")
    if captured:
        try:
            value = datetime.fromisoformat(str(captured).replace("Z", "+00:00"))
            video.captured_at = value.replace(tzinfo=UTC) if value.tzinfo is None else value
        except ValueError:
            pass


def delete_video_files(settings: Settings, video: Video) -> None:
    for rel in (video.file_path, video.playable_path):
        if rel:
            abs_path(settings, rel).unlink(missing_ok=True)
    shutil.rmtree(settings.derived_dir / video.id, ignore_errors=True)


def folder_exists(db: Session, folder_id: int | None) -> bool:
    return folder_id is None or db.get(Folder, folder_id) is not None


def video_to_dict(v: Video) -> dict[str, Any]:
    base = f"/api/videos/{v.id}"
    ver = v.asset_version
    return {
        "id": v.id,
        "title": v.title,
        "description": v.description,
        "source_video_id": v.source_video_id,
        "edit_params": v.edit_params,
        "edited_at": v.edited_at.isoformat() if v.edited_at else None,
        "rating": v.rating,
        "favorite": v.favorite,
        "captured_at": v.captured_at.isoformat() if v.captured_at else None,
        "original_name": v.original_name,
        "folder_id": v.folder_id,
        "status": v.status,
        "error": v.error,
        "size": v.size,
        "duration": v.duration,
        "width": v.width,
        "height": v.height,
        "fps": v.fps,
        "bitrate": v.bitrate,
        "container": v.container,
        "video_codec": v.video_codec,
        "audio_codec": v.audio_codec,
        "rotation": (v.meta or {}).get("rotation", 0),
        "cover_time": v.cover_time,
        "tags": sorted(t.name for t in v.tags),
        "created_at": v.created_at.isoformat(),
        "updated_at": v.updated_at.isoformat() if v.updated_at else None,
        "deleted_at": v.deleted_at.isoformat() if v.deleted_at else None,
        "stream_url": f"{base}/stream?v={ver}",
        "download_url": f"{base}/download",
        "poster_url": f"{base}/poster.jpg?v={ver}" if v.has_poster else None,
        "preview_url": f"{base}/preview.mp4?v={ver}" if v.has_preview else None,
        "thumbnails_url": f"{base}/thumbnails.vtt?v={ver}" if v.has_sprite else None,
    }
