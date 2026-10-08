from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import Settings
from ..content_search import search_content, track_current
from ..db import get_db
from ..errors import APIError
from ..jobs.manager import JobManager
from ..models import Job, SubtitleCue, SubtitleTrack
from ..storage import budget_transaction
from ..transcription import TranscriptionParams, available, check_enabled, estimate
from .deps import get_jobs, get_settings
from .scenes import ready_video, signature

router = APIRouter(prefix="/api", tags=["content-search"], dependencies=[Depends(require_auth)])


def active(db: Session, jobs: JobManager, video_id: str) -> list[Job]:
    return [
        j
        for j in db.scalars(select(Job).where(Job.kind == "transcribe"))
        if video_id in j.video_ids
        and (j.status in {"queued", "running", "paused"} or j.id in jobs.running)
    ]


@router.get("/videos/{video_id}/transcription")
def status(
    video_id: str,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    video = ready_video(db, video_id)
    track = db.scalar(
        select(SubtitleTrack).where(
            SubtitleTrack.video_id == video_id, SubtitleTrack.generated.is_(True)
        )
    )
    stale = bool(track and not track_current(settings, video, track))
    latest = next(
        (
            j
            for j in db.scalars(
                select(Job).where(Job.kind == "transcribe").order_by(Job.created_at.desc())
            )
            if video_id in j.video_ids
        ),
        None,
    )
    return {
        "enabled": settings.transcription_enabled,
        "available": available(settings),
        "model": settings.transcription_model,
        "max_hours": settings.transcription_max_hours,
        "has_audio": bool(video.audio_codec),
        "stale": stale,
        "track": {
            "id": track.id,
            "label": track.label,
            "language": track.language,
            "segments": db.scalar(
                select(func.count())
                .select_from(SubtitleCue)
                .where(SubtitleCue.track_id == track.id)
            )
            or 0,
        }
        if track and not stale
        else None,
        "job": jobs.describe(db, latest) if latest else None,
    }


@router.post("/videos/{video_id}/transcription")
def submit(
    video_id: str,
    body: TranscriptionParams,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    with budget_transaction(db):
        video = ready_video(db, video_id)
        check_enabled(settings, video)
        pending = active(db, jobs, video_id)
        if pending:
            return jobs.describe(db, pending[0])
        job = jobs.submit(
            db,
            "transcribe",
            {
                "signature": signature(settings, video),
                "language": body.language,
                "storage_bytes": estimate(video),
            },
            [video_id],
            priority=body.priority,
        )
        return jobs.describe(db, job)


@router.delete("/videos/{video_id}/transcription")
def clear(
    video_id: str, db: Session = Depends(get_db), jobs: JobManager = Depends(get_jobs)
) -> dict[str, bool]:
    with budget_transaction(db):
        ready_video(db, video_id)
        if active(db, jobs, video_id):
            raise APIError(409, "请先结束语音转写任务", code="transcription_busy")
        for track in db.scalars(
            select(SubtitleTrack).where(
                SubtitleTrack.video_id == video_id, SubtitleTrack.generated.is_(True)
            )
        ):
            db.delete(track)
        db.commit()
        return {"ok": True}


@router.get("/content-search")
def content_search(
    response: Response,
    q: str = Query("", max_length=512),
    video_id: str | None = Query(None, pattern=r"^[a-f0-9]{32}$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    if video_id:
        ready_video(db, video_id)
    return search_content(db, settings, q, video_id=video_id, page=page, page_size=page_size)
