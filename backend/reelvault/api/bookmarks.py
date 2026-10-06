from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentAuth, require_auth
from ..config import Settings
from ..db import get_db
from ..models import Bookmark, SceneAnalysis, Video
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


@router.get("/{video_id}/bookmarks")
def list_bookmarks(
    video_id: str,
    auth: CurrentAuth = Depends(require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    video = ready_video(db, video_id)
    current = signature(settings, video)
    marks = rows(db, video_id, auth.user_id)
    analysis = db.get(SceneAnalysis, video_id)
    scenes_valid = bool(
        analysis and analysis.signature == current and analysis.asset_version == video.asset_version
    )
    starts: dict[float, str] = {}
    if scenes_valid and analysis:
        starts = {
            0: "场景 1",
            **{cut["time"]: f"场景 {i + 2}" for i, cut in enumerate(analysis.cuts)},
        }
    for row in marks:
        if row.kind == "chapter" and row.signature == current and row.position < video.duration:
            starts[row.position] = row.title
    if starts and 0 not in starts:
        starts[0] = "开头"
    times = sorted(starts)
    chapters = [
        {
            "start": t,
            "end": times[i + 1] if i + 1 < len(times) else video.duration,
            "title": starts[t],
        }
        for i, t in enumerate(times)
    ]
    return {
        "bookmarks": [as_dict(row, current) for row in marks],
        "chapters": chapters,
        "chapters_stale": bool(analysis and not scenes_valid),
    }


def save(
    db: Session, settings: Settings, video: Video, body: BookmarkBody, row: Bookmark
) -> dict[str, Any]:
    if body.position > video.duration or (
        body.kind == "chapter" and body.position >= video.duration
    ):
        raise HTTPException(422, "书签时间不能超过视频时长，章节须在结尾之前")
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
        raise HTTPException(409, "每个视频最多保存 1000 个书签与手动章节")
    return save(db, settings, video, body, Bookmark(video_id=video_id, user_id=auth.user_id))


def owned(db: Session, video_id: str, bookmark_id: str, user_id: int) -> Bookmark:
    row = db.get(Bookmark, bookmark_id)
    if row is None or row.video_id != video_id or row.user_id != user_id:
        raise HTTPException(404, "书签不存在")
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
