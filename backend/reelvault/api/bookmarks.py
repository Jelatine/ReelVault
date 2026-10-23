from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentAuth, require_auth
from ..config import Settings
from ..db import get_db
from ..errors import APIError
from ..media.chapters import Chapter, combine
from ..media.ffmpeg import FFmpegError, ffprobe_json
from ..media.probe import embedded_chapters
from ..models import Bookmark, SceneAnalysis, Video
from ..original_response import local_original
from .deps import FiniteNumber, get_settings
from .scenes import ready_video, signature

router = APIRouter(prefix="/api/videos", tags=["bookmarks"])


class BookmarkBody(BaseModel):
    position: FiniteNumber = Field(ge=0)
    title: str = Field("", max_length=128)
    note: str = Field("", max_length=2000)
    kind: Literal["bookmark", "chapter"] = "bookmark"


def as_dict(row: Bookmark, current: list[Any]) -> dict[str, Any]:
    return {
        "id": row.id,
        "position": row.position,
        "title": row.title,
        "note": row.note,
        "kind": row.kind,
        "stale": row.signature != current,
    }


def rows(db: Session, video_id: str, user_id: int) -> list[Bookmark]:
    return list(
        db.scalars(
            select(Bookmark)
            .where(Bookmark.video_id == video_id, Bookmark.user_id == user_id)
            .order_by(Bookmark.position, Bookmark.created_at)
        )
    )


async def file_chapters(db: Session, settings: Settings, video: Video) -> list[Chapter]:
    """Chapters stored in the file; probed once for videos ingested before they were read."""
    if "chapters" not in (video.meta or {}):
        source, version = video.file_path, video.asset_version
        try:
            data = await asyncio.wait_for(
                ffprobe_json(settings.ffprobe, str(local_original(settings, source))), 30
            )
        except (APIError, FFmpegError, OSError, TimeoutError):
            return []
        db.refresh(video)
        if video.file_path != source or video.asset_version != version:
            return []
        video.meta = {**(video.meta or {}), "chapters": embedded_chapters(data)}
        db.commit()
    return list(video.meta["chapters"])


def timeline_marks(
    db: Session, video: Video, user_id: int, current: list[Any]
) -> tuple[list[tuple[float, str]], list[tuple[float, str]], bool]:
    """Scene and manually named chapter starts that apply to the current file."""
    analysis = db.get(SceneAnalysis, video.id)
    scenes_valid = bool(
        analysis and analysis.signature == current and analysis.asset_version == video.asset_version
    )
    scenes: list[tuple[float, str]] = []
    if scenes_valid and analysis:
        scenes = [(0.0, "场景 1")]
        scenes += [(cut["time"], f"场景 {i + 2}") for i, cut in enumerate(analysis.cuts)]
    manual = [
        (row.position, row.title)
        for row in rows(db, video.id, user_id)
        if row.kind == "chapter" and row.signature == current and row.position < video.duration
    ]
    return scenes, manual, bool(analysis and not scenes_valid)


def chapter_snapshot(
    db: Session, settings: Settings, video_ids: list[str], user_id: int
) -> dict[str, dict[str, Any]]:
    """Scene and manual chapters at submission; edits map them with the file's own chapters."""
    snapshot: dict[str, dict[str, Any]] = {}
    for video_id in video_ids:
        video = db.get(Video, video_id)
        if video is None:
            continue
        try:
            current = signature(settings, video)
        except APIError:
            current = None
        scenes, manual, _ = (
            timeline_marks(db, video, user_id, current) if current else ([], [], False)
        )
        snapshot[video_id] = {"scenes": scenes, "manual": manual}
    return snapshot


@router.get("/{video_id}/bookmarks")
async def list_bookmarks(
    video_id: str,
    auth: CurrentAuth = Depends(require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    video = ready_video(db, video_id)
    current = signature(settings, video)
    embedded = await file_chapters(db, settings, video)
    scenes, manual, stale = timeline_marks(db, video, auth.user_id, current)
    return {
        "bookmarks": [as_dict(row, current) for row in rows(db, video_id, auth.user_id)],
        "chapters": combine(video.duration, embedded, scenes, manual),
        "embedded_chapters": embedded,
        "chapters_stale": stale,
    }


@router.post("/{video_id}/bookmarks/import-chapters")
async def import_chapters(
    video_id: str,
    auth: CurrentAuth = Depends(require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, int]:
    """Copy the file's chapters into editable chapters, skipping starts already named."""
    video = ready_video(db, video_id)
    current = signature(settings, video)
    existing = rows(db, video_id, auth.user_id)
    taken = {round(row.position, 3) for row in existing if row.kind == "chapter"}
    created = 0
    for chapter in await file_chapters(db, settings, video):
        start = float(chapter["start"])
        if round(start, 3) in taken or start >= video.duration:
            continue
        if len(existing) + created >= 1000:
            raise APIError(409, "每个视频最多保存 1000 个书签与手动章节", code="bookmark_limit")
        db.add(
            Bookmark(
                video_id=video_id,
                user_id=auth.user_id,
                position=start,
                title=str(chapter["title"])[:128] or "章节",
                note="",
                kind="chapter",
                signature=current,
            )
        )
        taken.add(round(start, 3))
        created += 1
    db.commit()
    return {"created": created}


def save(
    db: Session, settings: Settings, video: Video, body: BookmarkBody, row: Bookmark
) -> dict[str, Any]:
    if body.position > video.duration or (
        body.kind == "chapter" and body.position >= video.duration
    ):
        raise APIError(
            422, "书签时间不能超过视频时长，章节须在结尾之前", code="bookmark_time_invalid"
        )
    row.position, row.title, row.note, row.kind = (
        body.position,
        body.title.strip() or ("章节" if body.kind == "chapter" else "书签"),
        body.note,
        body.kind,
    )
    row.signature = signature(settings, video)
    db.add(row)
    db.commit()
    return as_dict(row, row.signature)


@router.post("/{video_id}/bookmarks")
def create_bookmark(
    video_id: str,
    body: BookmarkBody,
    auth: CurrentAuth = Depends(require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    video = ready_video(db, video_id)
    if len(rows(db, video_id, auth.user_id)) >= 1000:
        raise APIError(409, "每个视频最多保存 1000 个书签与手动章节", code="bookmark_limit")
    return save(db, settings, video, body, Bookmark(video_id=video_id, user_id=auth.user_id))


def owned(db: Session, video_id: str, bookmark_id: str, user_id: int) -> Bookmark:
    row = db.get(Bookmark, bookmark_id)
    if row is None or row.video_id != video_id or row.user_id != user_id:
        raise APIError(404, "书签不存在", code="bookmark_not_found")
    return row


@router.put("/{video_id}/bookmarks/{bookmark_id}")
def update_bookmark(
    video_id: str,
    bookmark_id: str,
    body: BookmarkBody,
    auth: CurrentAuth = Depends(require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    video = ready_video(db, video_id)
    return save(db, settings, video, body, owned(db, video_id, bookmark_id, auth.user_id))


@router.delete("/{video_id}/bookmarks/{bookmark_id}")
def delete_bookmark(
    video_id: str,
    bookmark_id: str,
    auth: CurrentAuth = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    ready_video(db, video_id)
    db.delete(owned(db, video_id, bookmark_id, auth.user_id))
    db.commit()
    return {"ok": True}
