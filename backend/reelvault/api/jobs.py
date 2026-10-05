from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import Settings
from ..db import get_db
from ..jobs.manager import JobManager, job_to_dict
from ..library import abs_path
from ..media import ops
from ..models import Job, Video
from .deps import get_jobs, get_settings

router = APIRouter(prefix="/api", tags=["jobs"], dependencies=[Depends(require_auth)])


class OutputOptions(BaseModel):
    mode: Literal["new", "replace"] = "new"
    title: str | None = Field(None, max_length=255)


class EditBody(BaseModel):
    edit: ops.EditParams
    output: OutputOptions = OutputOptions()


@router.post("/videos/{video_id}/edit")
def submit_edit(
    video_id: str,
    body: EditBody,
    db: Session = Depends(get_db),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    ids = body.edit.video_ids if isinstance(body.edit, ops.MergeParams) else [video_id]
    if video_id not in ids:
        ids = [video_id, *ids]
    for vid in ids:
        video = db.get(Video, vid)
        if video is None or video.deleted_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "视频不存在")
        if video.status == "error":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"视频「{video.title}」无法处理")
    if isinstance(body.edit, ops.MergeParams):
        body.edit.video_ids = ids
    params = {"edit": body.edit.model_dump(), "output": body.output.model_dump()}
    job = jobs.submit(db, "edit", params, ids)
    return job_to_dict(job)


@router.get("/jobs")
def list_jobs(
    status_filter: str | None = Query(None, alias="status"),
    video_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = select(Job).order_by(Job.created_at.desc()).limit(limit)
    if status_filter:
        stmt = stmt.where(Job.status.in_(status_filter.split(",")))
    rows = db.scalars(stmt).all()
    if video_id:
        rows = [j for j in rows if video_id in j.video_ids or j.result_video_id == video_id]
    return [job_to_dict(j) for j in rows]


@router.get("/jobs/events")
async def job_events(request: Request, jobs: JobManager = Depends(get_jobs)) -> StreamingResponse:
    queue = jobs.subscribe()

    async def gen() -> AsyncIterator[str]:
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: job\ndata: {payload}\n\n"
                except TimeoutError:
                    yield ": ping\n\n"
        finally:
            jobs.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _get(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    return job


@router.get("/jobs/{job_id}")
def job_detail(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return job_to_dict(_get(db, job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str, db: Session = Depends(get_db), jobs: JobManager = Depends(get_jobs)
) -> dict[str, Any]:
    job = _get(db, job_id)
    jobs.cancel(db, job)
    return job_to_dict(job)


@router.get("/jobs/{job_id}/download")
def job_download(
    job_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> FileResponse:
    job = _get(db, job_id)
    if not job.result_file:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "该任务没有可下载的文件")
    path = abs_path(settings, job.result_file)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文件已被清理")
    return FileResponse(path, filename=job.params.get("name") or path.name)


@router.delete("/jobs")
def clear_finished(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, int]:
    finished = ("succeeded", "failed", "canceled")
    for job in db.scalars(
        select(Job).where(Job.status.in_(finished), Job.result_file.is_not(None))
    ):
        with contextlib.suppress(OSError):
            abs_path(settings, job.result_file or "").unlink(missing_ok=True)
    result = db.execute(delete(Job).where(Job.status.in_(finished)))
    db.commit()
    return {"deleted": result.rowcount or 0}  # type: ignore[attr-defined]
