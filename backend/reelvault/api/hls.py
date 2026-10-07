from __future__ import annotations

import re
import shutil
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import Settings
from ..db import get_db
from ..errors import APIError
from ..jobs.manager import JobManager
from ..media.hls import estimated_bytes
from ..media.probe import MediaInfo
from ..models import HlsPackage, Job, RuntimeSetting, Video
from ..storage import check_budget as check_disk_budget
from .deps import get_jobs, get_settings
from .scenes import ready_video, signature

router = APIRouter(tags=["hls"], dependencies=[Depends(require_auth)])
ACTIVE = ["queued", "running", "paused"]


def packages_size(db: Session) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(HlsPackage.size), 0))) or 0)


def check_budget(
    db: Session, settings: Settings, estimate: int, exclude_job: str | None = None
) -> None:
    reserved = sum(
        int(j.params.get("estimated_bytes", 0))
        for j in db.scalars(
            select(Job).where(Job.kind == "hls", Job.status.in_(ACTIVE), Job.id != exclude_job)
        )
    )
    check_disk_budget(db, settings, estimate, exclude_job=exclude_job)
    if packages_size(db) + reserved + estimate > settings.hls_max_cache_gb * 1024**3:
        raise APIError(409, "HLS 缓存将超过上限，请清理缓存或增加上限", code="hls_cache_limit")


def active_jobs(db: Session, jobs: JobManager, video_id: str | None = None) -> list[Job]:
    return [
        j
        for j in db.scalars(select(Job).where(Job.kind == "hls"))
        if (j.status in ACTIVE or j.id in jobs.running)
        and (video_id is None or video_id in j.video_ids)
    ]


def preferences(db: Session, settings: Settings) -> dict[str, Any]:
    return {
        "enabled": settings.hls_enabled,
        "min_size_mb": settings.hls_min_size_mb,
        "max_cache_gb": settings.hls_max_cache_gb,
        "cache_size": packages_size(db),
        "cache_count": db.scalar(select(func.count()).select_from(HlsPackage)),
    }


@router.get("/api/system/hls")
def hls_settings(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    return preferences(db, settings)


class HlsPreferences(BaseModel):
    enabled: bool
    min_size_mb: int = Field(256, ge=0, le=102400, strict=True)
    max_cache_gb: int = Field(20, ge=1, le=1024, strict=True)


@router.put("/api/system/hls")
def set_hls_settings(
    body: HlsPreferences,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    values = {
        "hls_enabled": body.enabled,
        "hls_min_size_mb": body.min_size_mb,
        "hls_max_cache_gb": body.max_cache_gb,
    }
    saved = db.get(RuntimeSetting, "hls")
    if saved is None:
        db.add(RuntimeSetting(key="hls", value=values))
    else:
        saved.value = values
    db.commit()
    for key, value in values.items():
        setattr(settings, key, value)
    if not body.enabled:
        for job in active_jobs(db, jobs):
            if job.status in ACTIVE:
                jobs.cancel(db, job)
    return preferences(db, settings)


def clear(db: Session, settings: Settings, jobs: JobManager, video_id: str | None) -> int:
    db.execute(text("BEGIN IMMEDIATE"))
    if active_jobs(db, jobs, video_id):
        raise APIError(409, "请先结束或取消 HLS 生成任务，再清理缓存", code="hls_busy")
    query = select(HlsPackage)
    if video_id is not None:
        query = query.where(HlsPackage.video_id == video_id)
    rows = list(db.scalars(query))
    ids = [p.video_id for p in rows]
    for package in rows:
        db.delete(package)
    for vid in ids:
        shutil.rmtree(settings.derived_dir / vid / "hls", ignore_errors=True)
    db.commit()
    return len(ids)


@router.delete("/api/system/hls/cache")
def clear_all(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, int]:
    return {"cleared": clear(db, settings, jobs, None)}


def status_data(db: Session, settings: Settings, jobs: JobManager, video: Video) -> dict[str, Any]:
    sig = signature(settings, video)
    package = db.get(HlsPackage, video.id)
    stale = bool(
        package
        and (
            package.signature != sig
            or not (
                settings.derived_dir / video.id / "hls" / package.generation / "master.m3u8"
            ).is_file()
        )
    )
    latest = next(
        (
            j
            for j in db.scalars(
                select(Job).where(Job.kind == "hls").order_by(Job.created_at.desc())
            )
            if video.id in j.video_ids
        ),
        None,
    )
    return {
        **preferences(db, settings),
        "stale": stale,
        "job": jobs.describe(db, latest) if latest else None,
        "job_stale": bool(latest and latest.params.get("signature") != sig),
        "package": {
            "url": f"/api/videos/{video.id}/hls/{package.generation}/master.m3u8",
            "size": package.size,
            "renditions": package.renditions,
        }
        if package is not None and not stale
        else None,
    }


@router.get("/api/videos/{video_id}/hls")
def get_hls(
    video_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    return status_data(db, settings, jobs, ready_video(db, video_id))


class HlsRequest(BaseModel):
    automatic: bool = False


@router.post("/api/videos/{video_id}/hls")
def submit_hls(
    video_id: str,
    body: HlsRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    db.execute(text("BEGIN IMMEDIATE"))
    video = ready_video(db, video_id)
    if not settings.hls_enabled:
        raise APIError(409, "HLS 已关闭，请先在设置中开启", code="hls_disabled")
    if body.automatic and video.size < settings.hls_min_size_mb * 1024**2:
        raise APIError(409, "此视频未达到自动生成大小阈值", code="hls_below_threshold")
    sig = signature(settings, video)
    active = active_jobs(db, jobs, video.id)
    if active:
        return jobs.describe(db, active[0])
    if status_data(db, settings, jobs, video)["package"]:
        return {"cached": True}
    info = MediaInfo(
        duration=video.duration,
        width=video.width,
        height=video.height,
        audio_codec=video.audio_codec,
    )
    check_budget(db, settings, estimated_bytes(info))
    return jobs.describe(
        db,
        jobs.submit(
            db, "hls", {"signature": sig, "estimated_bytes": estimated_bytes(info)}, [video.id]
        ),
    )


@router.delete("/api/videos/{video_id}/hls")
def clear_video(
    video_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, int]:
    ready_video(db, video_id)
    return {"cleared": clear(db, settings, jobs, video_id)}


@router.get("/api/videos/{video_id}/hls/{generation}/{asset:path}")
def serve_hls(
    video_id: str,
    generation: str,
    asset: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    video = ready_video(db, video_id)
    package = db.get(HlsPackage, video_id)
    if not settings.hls_enabled:
        raise APIError(409, "HLS 已关闭", code="hls_disabled")
    if (
        not package
        or generation != package.generation
        or not re.fullmatch(r"[a-f0-9]{32}", generation)
    ):
        raise APIError(404, "HLS 缓存不存在", code="hls_cache_not_found")
    if package.signature != signature(settings, video):
        raise APIError(409, "源文件已变化，请重新生成 HLS", code="hls_source_changed")
    if not re.fullmatch(r"master\.m3u8|v[0-2]/(?:index\.m3u8|seg_\d{6}\.ts)", asset):
        raise APIError(404, "HLS 资源不存在", code="hls_resource_not_found")
    root = (settings.derived_dir / video_id / "hls" / generation).resolve()
    path = (root / asset).resolve()
    if (
        not path.is_relative_to(settings.derived_dir.resolve())
        or not path.is_relative_to(root)
        or not path.is_file()
    ):
        raise APIError(404, "HLS 资源不存在", code="hls_resource_not_found")
    return FileResponse(
        path,
        media_type="application/vnd.apple.mpegurl" if path.suffix == ".m3u8" else "video/mp2t",
        headers={"Cache-Control": "private, no-cache"},
    )
