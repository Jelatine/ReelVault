"""Regenerate scrubbing sprites without re-running the whole ingest."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import get_db
from ..jobs.manager import JobManager
from ..models import Job, Video
from ..storage import MIB, budget_transaction
from .deps import get_jobs
from .scenes import ready_video

router = APIRouter(prefix="/api/videos", tags=["sprites"], dependencies=[Depends(require_auth)])

# Temporary sprite + VTT; the finished JPEG is a few hundred KiB at most.
STORAGE_BYTES = 16 * MIB


def active(db: Session, jobs: JobManager) -> list[Job]:
    return [
        j
        for j in db.scalars(select(Job).where(Job.kind == "sprite"))
        if j.status in {"queued", "running", "paused"} or j.id in jobs.running
    ]


def _request(video_id: str) -> tuple[str, dict[str, Any], list[str]]:
    return "sprite", {"storage_bytes": STORAGE_BYTES}, [video_id]


@router.post("/{video_id}/sprite")
def regenerate(
    video_id: str, db: Session = Depends(get_db), jobs: JobManager = Depends(get_jobs)
) -> dict[str, Any]:
    with budget_transaction(db):
        video = ready_video(db, video_id)
        pending = next((j for j in active(db, jobs) if video.id in j.video_ids), None)
        if pending:
            return jobs.describe(db, pending)
        return jobs.describe(db, jobs.submit(db, *_request(video.id)))


class BatchSpriteBody(BaseModel):
    # None regenerates every ready video in the library.
    video_ids: list[str] | None = Field(None, min_length=1, max_length=10000)


@router.post("/sprites")
def regenerate_many(
    body: BatchSpriteBody, db: Session = Depends(get_db), jobs: JobManager = Depends(get_jobs)
) -> dict[str, int]:
    with budget_transaction(db):
        query = select(Video.id).where(Video.deleted_at.is_(None), Video.status == "ready")
        if body.video_ids is not None:
            query = query.where(Video.id.in_(body.video_ids))
        ready = list(db.scalars(query))
        busy = {vid for j in active(db, jobs) for vid in j.video_ids}
        ids = [vid for vid in ready if vid not in busy]
        requested = len(set(body.video_ids)) if body.video_ids is not None else len(ready)
        if ids:
            jobs.submit_many(db, [_request(vid) for vid in ids], priority=0)
        return {"submitted": len(ids), "skipped": requested - len(ids)}
