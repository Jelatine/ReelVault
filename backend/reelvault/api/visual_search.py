from __future__ import annotations

import asyncio
import json
import math
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import Settings
from ..db import get_db
from ..errors import APIError
from ..jobs.manager import JobManager
from ..jobs.vision import estimate
from ..media.ffmpeg import FFmpegError, ffmpeg_args, run_command
from ..models import Job, VectorFrame, VideoVectorIndex, new_id
from ..storage import lock_budget
from ..vision import MODEL_ID, VisionClient, VisionParams, check_enabled
from ..visual_search import current, directory, rank
from .deps import get_jobs, get_settings
from .images import FORMATS
from .scenes import ready_video, signature

router = APIRouter(prefix="/api", tags=["visual-search"], dependencies=[Depends(require_auth)])


def active(db: Session, jobs: JobManager, video_id: str) -> list[Job]:
    return [
        job
        for job in db.scalars(
            select(Job).where(
                Job.kind == "vision_index",
                or_(Job.status.in_(["queued", "running", "paused"]), Job.id.in_(jobs.running)),
            )
        )
        if video_id in job.video_ids
    ]


def client(settings: Settings) -> VisionClient:
    check_enabled(settings)
    return VisionClient(settings.vision_url, settings.vision_token)


@router.get("/visual-search/status")
def service_status(
    response: Response, settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    available = False
    error = None
    if settings.vision_enabled:
        try:
            available = client(settings).request("/health").get("ready") is True
        except APIError as failure:
            error = failure.code
    return {
        "enabled": settings.vision_enabled,
        "available": available,
        "error": error,
        "model": MODEL_ID,
        "max_frames": settings.vision_max_frames,
    }


@router.get("/videos/{video_id}/visual-index")
def index_status(
    video_id: str,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    video = ready_video(db, video_id)
    index = db.get(VideoVectorIndex, video.id)
    valid = index is not None and current(settings, video, index)
    latest = db.scalar(
        select(Job)
        .where(Job.kind == "vision_index", Job.video_ids == [video.id])
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    return {
        "enabled": settings.vision_enabled,
        "stale": bool(index and not valid),
        "max_frames": settings.vision_max_frames,
        "index": {
            "frames": index.frames,
            "interval": index.interval,
            "indexed_at": index.indexed_at,
        }
        if index and valid
        else None,
        "job": jobs.describe(db, latest) if latest else None,
    }


@router.post("/videos/{video_id}/visual-index")
def submit(
    video_id: str,
    body: VisionParams,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    check_enabled(settings)
    lock_budget(db)
    video = ready_video(db, video_id)
    if not math.isfinite(video.duration) or not 0 < video.duration <= 24 * 3600:
        raise APIError(422, "画面索引仅支持最长 24 小时的视频", code="vision_duration_invalid")
    pending = active(db, jobs, video_id)
    if pending:
        return jobs.describe(db, pending[0])
    params = {
        **body.model_dump(exclude={"priority"}),
        "signature": signature(settings, video),
        "storage_bytes": estimate(video.duration, body, settings.vision_max_frames),
    }
    return jobs.describe(
        db, jobs.submit(db, "vision_index", params, [video_id], priority=body.priority)
    )


@router.delete("/videos/{video_id}/visual-index")
async def clear(
    video_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, bool]:
    lock_budget(db)
    ready_video(db, video_id)
    if active(db, jobs, video_id):
        raise APIError(409, "请先结束画面索引任务", code="vision_index_busy")
    index = db.get(VideoVectorIndex, video_id)
    if index:
        path = directory(settings, index)
        db.delete(index)
        db.commit()
        await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)
    return {"ok": True}


@router.get("/visual-search")
def text_search(
    response: Response,
    q: str = Query(min_length=1, max_length=512),
    video_id: str | None = Query(None, pattern=r"^[a-f0-9]{32}$"),
    page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    if video_id:
        ready_video(db, video_id)
    values = client(settings).text(q)
    return rank(db, settings, values, video_id, page, page_size)


@router.post("/visual-search/image")
async def image_search(
    file: UploadFile,
    response: Response,
    video_id: str | None = Form(None, pattern=r"^[a-f0-9]{32}$"),
    page: int = Form(1, ge=1, le=100000),
    page_size: int = Form(30, ge=1, le=100),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    model = client(settings)
    if video_id:
        ready_video(db, video_id)
    suffix = Path(file.filename or "").suffix.lower()
    temp = settings.tmp_dir / f"vision-query-{new_id()}"
    temp.mkdir(mode=0o700, parents=True)
    try:
        if suffix not in FORMATS:
            raise APIError(400, "请选择 PNG、JPEG 或 WebP 图片", code="image_format_unsupported")
        data = await file.read(10 * 1024 * 1024 + 1)
        if not data:
            raise APIError(400, "图片文件为空", code="image_empty")
        if len(data) > 10 * 1024 * 1024:
            raise APIError(413, "图片不能超过 10 MiB", code="image_too_large")
        source, target = temp / ("input" + suffix), temp / "query.jpg"
        await asyncio.to_thread(source.write_bytes, data)
        probe = await asyncio.wait_for(
            run_command(
                [
                    settings.ffprobe,
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-f",
                    FORMATS[suffix],
                    "-show_streams",
                    "-of",
                    "json",
                    str(source),
                ]
            ),
            30,
        )
        stream: dict[str, Any] = next(
            (
                s
                for s in json.loads(probe.stdout).get("streams", [])
                if s.get("codec_type") == "video"
            ),
            {},
        )
        width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
        if min(width, height) <= 0 or max(width, height) > 4096:
            raise APIError(400, "图片尺寸不能超过 4096×4096", code="image_dimensions_exceeded")
        await asyncio.wait_for(
            run_command(
                ffmpeg_args(
                    settings.ffmpeg,
                    [
                        "-protocol_whitelist",
                        "file,pipe",
                        "-f",
                        FORMATS[suffix],
                        "-i",
                        str(source),
                        "-vf",
                        "scale=512:512:force_original_aspect_ratio=decrease",
                        "-frames:v",
                        "1",
                        "-q:v",
                        "3",
                        str(target),
                    ],
                    progress=False,
                )
            ),
            30,
        )
        image = await asyncio.to_thread(target.read_bytes)
        values = await asyncio.to_thread(model.image, image)
        return await asyncio.to_thread(rank, db, settings, values, video_id, page, page_size)
    except (FFmpegError, ValueError, TimeoutError) as error:
        raise APIError(400, "图片无法解码", code="image_decode_failed") from error
    finally:
        await file.close()
        await asyncio.to_thread(shutil.rmtree, temp, ignore_errors=True)


@router.get("/visual-search/frames/{frame_id}")
def frame_image(
    frame_id: int,
    generation: str = Query(pattern=r"^[a-f0-9]{32}$"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    frame = db.get(VectorFrame, frame_id)
    if not frame:
        raise APIError(404, "画面不存在", code="vision_frame_missing")
    video = ready_video(db, frame.video_id)
    index = db.get(VideoVectorIndex, video.id)
    if not index or index.generation != generation or not current(settings, video, index):
        raise APIError(404, "画面索引已过期", code="vision_frame_missing")
    path = directory(settings, index) / f"{frame.ordinal:04d}.jpg"
    if not path.is_file() or path.is_symlink():
        raise APIError(404, "画面缓存不存在，请重新生成索引", code="vision_frame_missing")
    return FileResponse(
        path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"}
    )
