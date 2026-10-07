from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import Settings
from ..db import get_db
from ..errors import APIError
from ..jobs.manager import JobManager
from ..library import abs_path
from ..models import Job
from ..playback_cache import cached_path, estimate, needs_copy, remove_copy
from ..storage import lock_budget
from .deps import get_jobs, get_settings
from .scenes import ready_video, signature

router = APIRouter(prefix="/api/videos", tags=["playback"], dependencies=[Depends(require_auth)])


def active(db: Session, jobs: JobManager, video_id: str) -> list[Job]:
    return [
        j
        for j in db.scalars(select(Job).where(Job.kind == "playable"))
        if video_id in j.video_ids
        and (j.status in {"queued", "running", "paused"} or j.id in jobs.running)
    ]


@router.get("/{video_id}/playback-cache")
def status(
    video_id: str,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    video = ready_video(db, video_id)
    copy = cached_path(settings, video)
    try:
        size = copy.stat().st_size if copy else 0
    except OSError:
        copy, size = None, 0
    latest = next(
        (
            j
            for j in db.scalars(
                select(Job).where(Job.kind == "playable").order_by(Job.created_at.desc())
            )
            if video.id in j.video_ids
        ),
        None,
    )
    return {
        "required": needs_copy(video),
        "ready": not needs_copy(video) or copy is not None,
        "cached": copy is not None,
        "size": size,
        "stale": bool(video.playable_path and copy is None),
        "job": jobs.describe(db, latest) if latest else None,
    }


@router.post("/{video_id}/playback-cache")
def submit(
    video_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    lock_budget(db)
    video = ready_video(db, video_id)
    pending = active(db, jobs, video_id)
    if pending:
        return jobs.describe(db, pending[0])
    if not needs_copy(video) or cached_path(settings, video):
        return {"cached": True}
    return jobs.describe(
        db,
        jobs.submit(
            db,
            "playable",
            {
                "signature": signature(settings, video),
                "storage_bytes": estimate(video),
            },
            [video_id],
        ),
    )


@router.delete("/{video_id}/playback-cache")
def clear(
    video_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, bool]:
    lock_budget(db)
    video = ready_video(db, video_id)
    # Ingest writes the legacy eager path, so it must also finish before cleanup.
    if active(db, jobs, video_id) or any(
        video_id in j.video_ids
        for j in db.scalars(
            select(Job).where(Job.kind == "ingest", Job.status.in_(["queued", "running", "paused"]))
        )
    ):
        raise APIError(409, "请先结束播放缓存生成任务", code="playback_cache_busy")
    old = abs_path(settings, video.playable_path) if video.playable_path else None
    remove_copy(settings, video_id, old)
    video.playable_path = None
    video.meta = {k: v for k, v in (video.meta or {}).items() if k != "playable_signature"}
    video.asset_version += 1
    db.commit()
    return {"cleared": True}
