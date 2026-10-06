from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from ..auth import CurrentAuth, require_auth
from ..db import get_db
from ..models import Playback, Video, utcnow
from .deps import FiniteNumber
from .videos import get_video

router = APIRouter(prefix="/api", tags=["playback"])


def playback_dict(row: Playback | None) -> dict[str, Any]:
    return {
        "position": row.position if row else 0,
        "play_count": row.play_count if row else 0,
        "last_played_at": row.last_played_at.isoformat() if row else None,
    }


@router.get("/playback")
def history(
    auth: CurrentAuth = Depends(require_auth), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(Playback)
        .join(Video, Video.id == Playback.video_id)
        .where(Playback.user_id == auth.user_id, Video.deleted_at.is_(None))
        .order_by(Playback.last_played_at.desc())
        .limit(100)
    )
    return [{"video_id": row.video_id, **playback_dict(row)} for row in rows]


@router.get("/videos/{video_id}/playback")
def get_playback(
    video_id: str,
    auth: CurrentAuth = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    get_video(db, video_id)
    return playback_dict(db.get(Playback, (auth.user_id, video_id)))


class PositionBody(BaseModel):
    position: FiniteNumber = Field(ge=0)


def record(
    db: Session, user_id: int, video_id: str, *, position: float | None = None, start: bool = False
) -> dict[str, Any]:
    video = get_video(db, video_id)
    now = utcnow()
    values: dict[str, Any] = {"last_played_at": now}
    if position is not None:
        values["position"] = min(position, video.duration)
    if start:
        values["play_count"] = Playback.play_count + 1
    stmt = insert(Playback).values(
        user_id=user_id,
        video_id=video_id,
        position=values.get("position", 0),
        play_count=1 if start else 0,
        last_played_at=now,
    )
    db.execute(stmt.on_conflict_do_update(index_elements=["user_id", "video_id"], set_=values))
    db.commit()
    return playback_dict(db.get(Playback, (user_id, video_id)))


@router.post("/videos/{video_id}/playback/start")
def start_playback(
    video_id: str,
    auth: CurrentAuth = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return record(db, auth.user_id, video_id, start=True)


@router.put("/videos/{video_id}/playback")
def save_playback(
    video_id: str,
    body: PositionBody,
    auth: CurrentAuth = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return record(db, auth.user_id, video_id, position=body.position)
