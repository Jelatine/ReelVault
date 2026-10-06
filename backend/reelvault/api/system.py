from __future__ import annotations

import shutil
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .. import __version__
from ..auth import require_auth
from ..backup import create_backup
from ..config import Settings
from ..db import get_db
from ..jobs.manager import JobManager
from ..library import VIDEO_EXTENSIONS, stem_of, store_file
from ..models import Job, RuntimeSetting, Video
from ..updates import UpdateError, Updater
from .deps import get_jobs, get_settings

router = APIRouter(tags=["system"])


@router.get("/api/system/encoding", dependencies=[Depends(require_auth)])
def encoding_status(
    settings: Settings = Depends(get_settings), jobs: JobManager = Depends(get_jobs)
) -> dict[str, Any]:
    return jobs.encoding.status(settings.encoder)


class EncodingPreference(BaseModel):
    encoder: Literal["software", "auto", "videotoolbox", "qsv", "vaapi", "nvenc"]


@router.put("/api/system/encoding", dependencies=[Depends(require_auth)])
def select_encoding(
    body: EncodingPreference,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    if body.encoder not in ("software", "auto"):
        family = next(
            f
            for f in jobs.encoding.status(settings.encoder)["families"]
            if f["value"] == body.encoder
        )
        if not any(encoder["compiled"] for encoder in family["encoders"]):
            raise HTTPException(400, "当前 ffmpeg 未编译该硬件编码器")
    saved = db.get(RuntimeSetting, "encoding")
    if saved is None:
        saved = RuntimeSetting(key="encoding", value={"encoder": body.encoder})
        db.add(saved)
    else:
        saved.value = {"encoder": body.encoder}
    db.commit()
    settings.encoder = body.encoder
    return jobs.encoding.status(settings.encoder)


@router.post("/api/system/backup", dependencies=[Depends(require_auth)])
def export_backup(settings: Settings = Depends(get_settings)) -> FileResponse:
    path = create_backup(settings)
    return FileResponse(
        path,
        filename=f"reelvault-{path.name}",
        media_type="application/zip",
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/api/system/info", dependencies=[Depends(require_auth)])
def info(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    usage = shutil.disk_usage(settings.data_dir)
    count, total_size = db.execute(
        select(func.count(), func.coalesce(func.sum(Video.size), 0)).where(
            Video.deleted_at.is_(None)
        )
    ).one()
    trash_count, trash_size = db.execute(
        select(func.count(), func.coalesce(func.sum(Video.size), 0)).where(
            Video.deleted_at.is_not(None)
        )
    ).one()
    return {
        "version": __version__,
        "ffmpeg_version": request.app.state.ffmpeg_version,
        "workers": settings.workers,
        "running_jobs": int(
            db.scalar(select(func.count()).select_from(Job).where(Job.status == "running")) or 0
        ),
        "queued_jobs": jobs.pending_count(),
        "paused_jobs": int(
            db.scalar(select(func.count()).select_from(Job).where(Job.status == "paused")) or 0
        ),
        "disk": {"total": usage.total, "used": usage.used, "free": usage.free},
        "library": {"count": count, "size": total_size},
        "trash": {"count": trash_count, "size": trash_size},
        "trash_retention_days": settings.trash_retention_days,
        "import_dir": str(settings.import_dir) if settings.import_dir else None,
    }


@router.post("/api/system/import", dependencies=[Depends(require_auth)])
def import_dir(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, int]:
    """Copy (hard-link when possible) videos from the configured import directory."""
    root = settings.import_dir
    if root is None or not root.is_dir():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "未配置导入目录 REELVAULT_IMPORT_DIR")
    known = set(db.scalars(select(Video.source_path).where(Video.source_path.is_not(None))))
    imported = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        if str(path) in known:
            continue
        video = store_file(
            db,
            settings,
            path,
            title=stem_of(path.name),
            original_name=path.name,
            folder_id=None,
            move=False,
            source_path=str(path),
        )
        jobs.submit(db, "ingest", {}, [video.id])
        imported += 1
    return {"imported": imported}


def get_updater(request: Request) -> Updater:
    updater: Updater = request.app.state.updater
    return updater


@router.get("/api/system/update", dependencies=[Depends(require_auth)])
def update_status(updater: Updater = Depends(get_updater)) -> dict[str, Any]:
    return updater.status()


@router.post("/api/system/update/check", dependencies=[Depends(require_auth)])
async def update_check(updater: Updater = Depends(get_updater)) -> dict[str, Any]:
    return await updater.check()


@router.post("/api/system/update/apply", dependencies=[Depends(require_auth)])
async def update_apply(updater: Updater = Depends(get_updater)) -> dict[str, Any]:
    try:
        await updater.start_upgrade()
    except UpdateError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return updater.status()
