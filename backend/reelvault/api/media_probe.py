"""Backfill stream details for videos ingested before the probe recorded them."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import get_db
from ..jobs.manager import JobManager
from ..models import Job, Video
from ..storage import budget_transaction
from .deps import get_jobs

router = APIRouter(
    prefix="/api/system/media-probe", tags=["media-probe"], dependencies=[Depends(require_auth)]
)

# Written by every probe since stream details were added, even when ffprobe reports none.
MARKER = "video_profile"


def missing(db: Session) -> list[str]:
    rows = db.execute(
        select(Video.id, Video.meta).where(Video.deleted_at.is_(None), Video.status == "ready")
    )
    return [vid for vid, meta in rows if MARKER not in (meta or {})]


def active(db: Session, jobs: JobManager) -> list[Job]:
    return [
        j
        for j in db.scalars(select(Job).where(Job.kind == "probe"))
        if j.status in {"queued", "running", "paused"} or j.id in jobs.running
    ]


@router.get("")
def status(db: Session = Depends(get_db), jobs: JobManager = Depends(get_jobs)) -> dict[str, int]:
    return {"missing": len(missing(db)), "active": len(active(db, jobs))}


@router.post("")
def backfill(db: Session = Depends(get_db), jobs: JobManager = Depends(get_jobs)) -> dict[str, Any]:
    with budget_transaction(db):
        busy = {vid for j in active(db, jobs) for vid in j.video_ids}
        ids = [vid for vid in missing(db) if vid not in busy]
        if ids:
            # The probe stores nothing; remote originals reserve their own download.
            jobs.submit_many(
                db, [("probe", {"storage_bytes": 0}, [vid]) for vid in ids], priority=0
            )
        return {"submitted": len(ids), "skipped": len(busy)}
