from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import Settings
from ..db import get_db
from ..errors import APIError
from ..jobs.manager import JobManager
from ..library import abs_path
from ..models import Job
from ..playback_cache import (
    HEVC_PATH,
    HEVC_SIGNATURE,
    cached_path,
    estimate,
    hevc_variant,
    needs_copy,
    remove_copy,
)
from ..storage import budget_transaction
from .deps import get_jobs, get_settings
from .scenes import ready_video, signature

router = APIRouter(prefix="/api/videos", tags=["playback"], dependencies=[Depends(require_auth)])


def variant(job: Job) -> str:
    return str(job.params.get("variant") or "h264")


def active(db: Session, jobs: JobManager, video_id: str, kind: str | None = None) -> list[Job]:
    return [
        j
        for j in db.scalars(select(Job).where(Job.kind == "playable"))
        if video_id in j.video_ids
        and (kind is None or variant(j) == kind)
        and (j.status in {"queued", "running", "paused"} or j.id in jobs.running)
    ]


@router.get("/{video_id}/playback-cache")
def status(
    video_id: str,
    response: Response,
    hevc: bool = Query(False),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    """``hevc`` is set by clients that play HEVC; they need at most a remux."""
    response.headers["Cache-Control"] = "no-store"
    video = ready_video(db, video_id)
    kind = "hevc" if hevc_variant(video, hevc) else "h264"
    copy = cached_path(settings, video, hevc)
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
            if video.id in j.video_ids and variant(j) == kind
        ),
        None,
    )
    recorded = (video.meta or {}).get(HEVC_PATH) if kind == "hevc" else video.playable_path
    return {
        "variant": kind,
        "required": needs_copy(video, hevc),
        "ready": not needs_copy(video, hevc) or copy is not None,
        "cached": copy is not None,
        "size": size,
        "stale": bool(recorded and copy is None),
        "job": jobs.describe(db, latest) if latest else None,
    }


@router.post("/{video_id}/playback-cache")
def submit(
    video_id: str,
    hevc: bool = Query(False),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    with budget_transaction(db):
        video = ready_video(db, video_id)
        kind = "hevc" if hevc_variant(video, hevc) else "h264"
        pending = active(db, jobs, video_id, kind)
        if pending:
            return jobs.describe(db, pending[0])
        if not needs_copy(video, hevc) or cached_path(settings, video, hevc):
            return {"cached": True}
        return jobs.describe(
            db,
            jobs.submit(
                db,
                "playable",
                {
                    "signature": signature(settings, video),
                    "storage_bytes": estimate(video, hevc),
                    "variant": kind,
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
    with budget_transaction(db):
        video = ready_video(db, video_id)
        # Ingest writes the legacy eager path, so it must also finish before cleanup.
        if active(db, jobs, video_id) or any(
            video_id in j.video_ids
            for j in db.scalars(
                select(Job).where(
                    Job.kind == "ingest", Job.status.in_(["queued", "running", "paused"])
                )
            )
        ):
            raise APIError(409, "请先结束播放缓存生成任务", code="playback_cache_busy")
        meta = video.meta or {}
        for relative in (video.playable_path, meta.get(HEVC_PATH)):
            remove_copy(settings, video_id, abs_path(settings, relative) if relative else None)
        video.playable_path = None
        dropped = {"playable_signature", HEVC_PATH, HEVC_SIGNATURE}
        video.meta = {k: v for k, v in meta.items() if k not in dropped}
        video.asset_version += 1
        db.commit()
        return {"cleared": True}
