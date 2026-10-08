"""Local copies of S3 originals: status, user-requested fetch and release."""

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
from ..locations import location_of
from ..models import Job, Video
from ..object_library import cache_pinned, cache_valid, release_cache
from ..storage import budget_transaction
from .deps import get_jobs, get_settings

router = APIRouter(prefix="/api/videos", tags=["originals"], dependencies=[Depends(require_auth)])
ACTIVE = ["queued", "running", "paused"]


def _video(db: Session, video_id: str) -> Video:
    video = db.get(Video, video_id)
    if video is None or video.deleted_at is not None:
        raise APIError(404, "视频不存在", code="video_not_found")
    return video


def _jobs(db: Session, jobs: JobManager, video_id: str, kind: str | None = None) -> list[Job]:
    query = select(Job).where(Job.status.in_(ACTIVE))
    if kind:
        query = query.where(Job.kind == kind)
    return [job for job in db.scalars(query) if video_id in job.video_ids]


@router.get("/{video_id}/original-cache")
def status(
    video_id: str,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    video = _video(db, video_id)
    remote = location_of(video.file_path) == "s3"
    ref = settings.s3_objects.get(video.file_path)
    try:
        local = abs_path(settings, video.file_path)
        cached = ref is not None and cache_valid(local, ref)
        pinned = cached and cache_pinned(local)
    except OSError:
        cached = pinned = False
    latest = next(
        (
            job
            for job in db.scalars(
                select(Job).where(Job.kind == "original_cache").order_by(Job.created_at.desc())
            )
            if video_id in job.video_ids
        ),
        None,
    )
    return {
        "remote": remote,
        # Not yet archived: the local file is still the only copy.
        "archived": ref is not None,
        "cached": cached,
        "pinned": pinned,
        "keep_local": bool(settings.s3 and settings.s3.keep_local),
        "size": ref.size if ref else video.size,
        "job": jobs.describe(db, latest) if latest else None,
    }


@router.post("/{video_id}/original-cache")
def fetch(
    video_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    with budget_transaction(db):
        video = _video(db, video_id)
        ref = settings.s3_objects.get(video.file_path)
        if ref is None:
            raise APIError(409, "该视频的原文件不在对象存储中", code="original_not_remote")
        pending = _jobs(db, jobs, video_id, "original_cache")
        if pending:
            return jobs.describe(db, pending[0])
        # ensure_original reserves the download size itself while transferring.
        return jobs.describe(
            db, jobs.submit(db, "original_cache", {"storage_bytes": 0}, [video_id])
        )


@router.delete("/{video_id}/original-cache")
def release(
    video_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, bool]:
    with budget_transaction(db):
        video = _video(db, video_id)
        if video.file_path not in settings.s3_objects:
            raise APIError(
                409, "原视频尚未保存到对象存储，不能释放本地副本", code="original_not_remote"
            )
        shared = select(Video.id).where(Video.file_path == video.file_path)
        if any(_jobs(db, jobs, other) for other in db.scalars(shared)):
            raise APIError(409, "请先结束使用该视频的任务", code="original_cache_busy")
        release_cache(settings, video.file_path)
        return {"released": True}
