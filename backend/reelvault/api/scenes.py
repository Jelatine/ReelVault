from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import Settings
from ..db import get_db
from ..jobs.manager import JobManager
from ..library import abs_path
from ..media.scenes import SceneParams, scene_chapters, source_signature
from ..models import Job, SceneAnalysis, Video
from .deps import get_jobs, get_settings

router = APIRouter(prefix="/api/videos", tags=["scenes"], dependencies=[Depends(require_auth)])


def ready_video(db: Session, video_id: str) -> Video:
    video = db.get(Video, video_id)
    if video is None or video.deleted_at:
        raise HTTPException(404, "视频不存在")
    if video.status != "ready":
        raise HTTPException(409, "视频尚未就绪")
    return video


def signature(settings: Settings, video: Video) -> list[Any]:
    try:
        return source_signature(abs_path(settings, video.file_path))
    except OSError as error:
        raise HTTPException(409, "源视频文件不可用") from error


@router.get("/{video_id}/scenes")
def get_scenes(
    video_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    video = ready_video(db, video_id)
    analysis = db.get(SceneAnalysis, video_id)
    stale = bool(
        analysis
        and (
            analysis.asset_version != video.asset_version
            or analysis.signature != signature(settings, video)
        )
    )
    latest = next(
        (
            job
            for job in db.scalars(
                select(Job).where(Job.kind == "scenes").order_by(Job.created_at.desc())
            )
            if video_id in job.video_ids
        ),
        None,
    )
    return {
        "job": jobs.describe(db, latest) if latest else None,
        "analysis": None
        if not analysis or stale
        else {
            "threshold": analysis.threshold,
            "min_interval": analysis.min_interval,
            "cuts": analysis.cuts,
            "chapters": scene_chapters(analysis.cuts, analysis.duration),
            "detected_at": analysis.detected_at.isoformat(),
        },
        "stale": stale,
    }


@router.post("/{video_id}/scenes")
def submit_scenes(
    video_id: str,
    body: SceneParams,
    db: Session = Depends(get_db),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    video = ready_video(db, video_id)
    # Filter JSON ids in Python, as in the general job list; different videos remain independent.
    for candidate in db.scalars(
        select(Job).where(Job.kind == "scenes", Job.status.in_(["queued", "running", "paused"]))
    ):
        if video_id in candidate.video_ids:
            active = candidate
            if all(active.params.get(key) == value for key, value in body.model_dump().items()):
                return jobs.describe(db, active)
            raise HTTPException(409, "已有未结束的场景检测，请完成或取消后调整参数")
    job = jobs.submit(
        db,
        "scenes",
        {
            **body.model_dump(),
            "asset_version": video.asset_version,
            "signature": signature(jobs.settings, video),
        },
        [video_id],
    )
    return jobs.describe(db, job)
