from __future__ import annotations

import asyncio
import contextlib
import copy
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, TypeAdapter, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import Settings
from ..db import get_db
from ..errors import APIError
from ..jobs.manager import JobManager
from ..library import abs_path
from ..media import ops
from ..media.assets import AssetError, audio_asset, image_asset, lut_asset, subtitle_asset
from ..models import EditPreset, Job, Video
from ..storage import budget_transaction, check_budget, job_bytes, job_requirements
from .deps import get_jobs, get_settings

router = APIRouter(prefix="/api", tags=["jobs"], dependencies=[Depends(require_auth)])


class OutputOptions(BaseModel):
    storage_id: str | None = Field(default=None, pattern=r"^(local|[a-f0-9]{32})$")
    mode: Literal["new", "replace"] = "new"
    title: str | None = Field(None, max_length=255)


class EditBody(BaseModel):
    edit: ops.EditParams
    output: OutputOptions = OutputOptions()
    priority: int = Field(1, ge=0, le=2, strict=True)


def asset_params(db: Session, settings: Settings, edit: ops.EditParams) -> dict[str, Any]:
    if not isinstance(
        edit, (ops.AudioParams, ops.SubtitleParams, ops.WatermarkParams, ops.AdjustParams)
    ):
        return {}
    try:
        if isinstance(edit, ops.AudioParams):
            asset = audio_asset(db, settings, edit)
        elif isinstance(edit, ops.SubtitleParams):
            asset = subtitle_asset(db, settings, edit)
        elif isinstance(edit, ops.AdjustParams):
            asset = lut_asset(db, settings, edit)
        else:
            asset = image_asset(db, settings, edit)
    except AssetError as error:
        raise APIError(400, str(error), code="asset_invalid") from error
    key = f"{asset.kind}_sha256" if asset else ""
    return {key: asset.sha256} if asset else {}


def validate_sources(db: Session, ids: list[str]) -> None:
    for vid in ids:
        video = db.get(Video, vid)
        if video is None or video.deleted_at is not None:
            raise APIError(status.HTTP_404_NOT_FOUND, "视频不存在", code="video_not_found")
        if video.status == "error":
            raise APIError(
                status.HTTP_400_BAD_REQUEST,
                f"视频「{video.title}」无法处理",
                code="video_unprocessable",
                params={"title": video.title},
            )


def preset_dict(preset: EditPreset) -> dict[str, Any]:
    return {"id": preset.id, "name": preset.name, "edit": preset.edit}


class PresetBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    edit: ops.EditParams

    @model_validator(mode="after")
    def reusable(self) -> PresetBody:
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("预设名称不能为空")
        if isinstance(self.edit, (ops.MergeParams, ops.CompositeParams)):
            raise ValueError("此操作涉及多个源视频，请在对应编辑器中设置")
        return self


@router.get("/edit-presets")
def list_presets(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [preset_dict(p) for p in db.scalars(select(EditPreset).order_by(EditPreset.name))]


def save_preset(db: Session, body: PresetBody, preset: EditPreset | None = None) -> EditPreset:
    existing = db.scalar(select(EditPreset).where(EditPreset.name == body.name))
    if existing is not None and (preset is None or existing.id != preset.id):
        raise APIError(409, "同名预设已存在", code="preset_name_conflict")
    if preset is None:
        preset = EditPreset(name=body.name, edit=body.edit.model_dump())
        db.add(preset)
    else:
        preset.name, preset.edit = body.name, body.edit.model_dump()
    db.commit()
    return preset


@router.post("/edit-presets")
def create_preset(
    body: PresetBody, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    asset_params(db, settings, body.edit)
    return preset_dict(save_preset(db, body))


@router.put("/edit-presets/{preset_id}")
def update_preset(
    preset_id: int,
    body: PresetBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    preset = db.get(EditPreset, preset_id)
    if preset is None:
        raise APIError(404, "预设不存在", code="preset_not_found")
    asset_params(db, settings, body.edit)
    return preset_dict(save_preset(db, body, preset))


@router.delete("/edit-presets/{preset_id}")
def delete_preset(preset_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    preset = db.get(EditPreset, preset_id)
    if preset is None:
        raise APIError(404, "预设不存在", code="preset_not_found")
    db.delete(preset)
    db.commit()
    return {"ok": True}


class BatchEditBody(BaseModel):
    video_ids: list[str] = Field(min_length=1, max_length=1000)
    edit: ops.EditParams | None = None
    preset_id: int | None = None
    output: OutputOptions = OutputOptions()
    priority: int = Field(1, ge=0, le=2, strict=True)

    @model_validator(mode="after")
    def one_source(self) -> BatchEditBody:
        if (self.edit is None) == (self.preset_id is None):
            raise ValueError("请提供编辑参数或选择一个预设")
        if isinstance(self.edit, (ops.MergeParams, ops.CompositeParams)):
            raise ValueError("批处理为每个视频创建独立任务，多源操作请使用对应编辑器")
        return self


@router.post("/jobs/batch")
def batch_edit(
    body: BatchEditBody, db: Session = Depends(get_db), jobs: JobManager = Depends(get_jobs)
) -> list[dict[str, Any]]:
    edit = body.edit
    if body.preset_id is not None:
        preset = db.get(EditPreset, body.preset_id)
        if preset is None:
            raise APIError(404, "预设不存在", code="preset_not_found")
        edit = TypeAdapter(ops.EditParams).validate_python(preset.edit)
    assert edit is not None
    if isinstance(edit, ops.AnimationParams) and body.output.mode == "replace":
        raise APIError(400, "动图为下载文件，不能替换原视频", code="animation_cannot_replace")
    if isinstance(edit, (ops.MergeParams, ops.CompositeParams)):
        raise APIError(
            400, "多源操作不能用于每视频独立批处理", code="batch_multi_source_unsupported"
        )
    ids = list(dict.fromkeys(body.video_ids))
    validate_sources(db, ids)
    params = {
        "edit": edit.model_dump(),
        "output": body.output.model_dump(),
        **asset_params(db, jobs.settings, edit),
    }
    submitted = jobs.submit_many(
        db, [("edit", params, [vid]) for vid in ids], priority=body.priority
    )
    return [jobs.describe(db, job) for job in submitted]


@router.post("/videos/{video_id}/edit")
def submit_edit(
    video_id: str,
    body: EditBody,
    db: Session = Depends(get_db),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    ids = (
        body.edit.video_ids
        if isinstance(body.edit, (ops.MergeParams, ops.CompositeParams))
        else [video_id]
    )
    if isinstance(body.edit, ops.AnimationParams) and body.output.mode == "replace":
        raise APIError(400, "动图为下载文件，不能替换原视频", code="animation_cannot_replace")
    if isinstance(body.edit, ops.CompositeParams):
        if body.output.mode == "replace":
            raise APIError(400, "多源拼接必须另存为新视频", code="composite_must_save_as")
        if video_id not in ids:
            raise APIError(400, "当前视频必须包含在拼接输入中", code="composite_source_required")
    if video_id not in ids:
        ids = [video_id, *ids]
    validate_sources(db, ids)
    if isinstance(body.edit, (ops.MergeParams, ops.CompositeParams)):
        body.edit.video_ids = ids
    params = {
        "edit": body.edit.model_dump(),
        "output": body.output.model_dump(),
        **asset_params(db, jobs.settings, body.edit),
    }
    job = jobs.submit(db, "edit", params, ids, priority=body.priority)
    return jobs.describe(db, job)


@router.get("/jobs")
def list_jobs(
    status_filter: str | None = Query(None, alias="status"),
    video_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    jobs: JobManager = Depends(get_jobs),
) -> list[dict[str, Any]]:
    stmt = select(Job).order_by(Job.created_at.desc()).limit(limit)
    if status_filter:
        stmt = stmt.where(Job.status.in_(status_filter.split(",")))
    rows = db.scalars(stmt).all()
    if video_id:
        rows = [j for j in rows if video_id in j.video_ids or j.result_video_id == video_id]
    return [jobs.describe(db, j) for j in rows]


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
        raise APIError(status.HTTP_404_NOT_FOUND, "任务不存在", code="job_not_found")
    return job


@router.get("/jobs/{job_id}")
def job_detail(
    job_id: str, db: Session = Depends(get_db), jobs: JobManager = Depends(get_jobs)
) -> dict[str, Any]:
    return jobs.describe(db, _get(db, job_id))


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str, db: Session = Depends(get_db), jobs: JobManager = Depends(get_jobs)
) -> dict[str, Any]:
    job = _get(db, job_id)
    jobs.cancel(db, job)
    return jobs.describe(db, job)


@router.post("/jobs/{job_id}/pause")
async def pause_job(
    job_id: str, db: Session = Depends(get_db), jobs: JobManager = Depends(get_jobs)
) -> dict[str, Any]:
    job = _get(db, job_id)
    try:
        jobs.pause(db, job)
    except ValueError as error:
        raise APIError(409, str(error), code="job_pause_conflict") from error
    return jobs.describe(db, job)


@router.post("/jobs/{job_id}/resume")
async def resume_job(
    job_id: str, db: Session = Depends(get_db), jobs: JobManager = Depends(get_jobs)
) -> dict[str, Any]:
    job = _get(db, job_id)
    try:
        jobs.resume(db, job)
    except ValueError as error:
        raise APIError(409, str(error), code="job_resume_conflict") from error
    return jobs.describe(db, job)


class PriorityBody(BaseModel):
    priority: int = Field(ge=0, le=2, strict=True)


@router.put("/jobs/{job_id}/priority")
async def set_priority(
    job_id: str,
    body: PriorityBody,
    db: Session = Depends(get_db),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    job = _get(db, job_id)
    if job.status not in {"queued", "paused"} or job.started_at:
        raise APIError(409, "只能调整尚未启动的任务优先级", code="job_priority_locked")
    job.priority = body.priority
    db.commit()
    jobs.enqueue(job.id)
    jobs.publish(job)
    return jobs.describe(db, job)


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    job_id: str,
    db: Session = Depends(get_db),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    with budget_transaction(db):
        old = _get(db, job_id)
        if old.status != "failed":
            raise APIError(409, "只有失败任务可以重试", code="job_retry_requires_failure")
        if old.result_video_id or old.result_file:
            raise APIError(
                409, "该任务已保存输出，请查看结果，避免重复修改", code="job_output_already_saved"
            )
        if old.kind not in jobs.handlers:
            raise APIError(409, "此任务类型不支持重试", code="job_retry_unsupported")
        if db.scalar(
            select(Job.id).where(
                Job.retry_of == old.id, Job.status.in_(["queued", "running", "paused"])
            )
        ):
            raise APIError(409, "该任务已有未结束的重试", code="job_retry_pending")
        for vid in old.video_ids:
            video = db.get(Video, vid)
            if video is None or (
                video.deleted_at and not old.params.get("history_replay") and old.kind != "ingest"
            ):
                raise APIError(404, "源视频不存在或已删除", code="source_video_not_found")
            if old.kind == "ingest":
                video.status, video.error = "processing", None
        params = copy.deepcopy(old.params)
        if old.kind == "ai_analyze":
            from ..ai import AiParams
            from .ai import active as ai_active
            from .ai import submission as ai_submission

            if ai_active(db, jobs, old.video_ids[0], all_indexes=True):
                raise APIError(409, "请先结束 AI 分析任务", code="ai_busy")
            params = ai_submission(
                db, jobs.settings, old.video_ids[0], AiParams.model_validate(params)
            )
        if old.kind == "vision_index":
            from ..jobs.vision import estimate as vision_estimate
            from ..vision import VisionParams
            from ..vision import check_enabled as vision_check_enabled
            from .scenes import ready_video, signature
            from .visual_search import active

            vision_check_enabled(jobs.settings)
            video = ready_video(db, old.video_ids[0])
            if active(db, jobs, video.id):
                raise APIError(409, "请先结束画面索引任务", code="vision_index_busy")
            from .ai import active as ai_active

            if ai_active(db, jobs, video.id):
                raise APIError(409, "请先结束 AI 分析任务", code="ai_busy")
            params["signature"] = signature(jobs.settings, video)
            params["storage_bytes"] = vision_estimate(
                video.duration, VisionParams.model_validate(params), jobs.settings.vision_max_frames
            )
        if old.kind == "transcribe":
            from ..transcription import check_enabled, estimate
            from .scenes import ready_video, signature
            from .transcription import active

            video = ready_video(db, old.video_ids[0])
            check_enabled(jobs.settings, video)
            if active(db, jobs, video.id):
                raise APIError(409, "请先结束语音转写任务", code="transcription_busy")
            params["signature"] = signature(jobs.settings, video)
            params["storage_bytes"] = estimate(video)
        if old.kind == "playable":
            from ..playback_cache import cached_path, estimate, needs_copy
            from .playback_cache import active
            from .scenes import ready_video, signature

            video = ready_video(db, old.video_ids[0])
            if active(db, jobs, video.id):
                raise APIError(409, "请先结束播放缓存生成任务", code="playback_cache_busy")
            if not needs_copy(video) or cached_path(jobs.settings, video):
                raise APIError(
                    409,
                    "该任务已保存输出，请查看结果，避免重复修改",
                    code="job_output_already_saved",
                )
            params["signature"] = signature(jobs.settings, video)
            params["storage_bytes"] = estimate(video)
        if old.kind == "link_import":
            from ..link_download import downloader_command
            from ..storage import MIB

            if not jobs.settings.link_import_enabled:
                raise APIError(403, "链接导入尚未启用", code="link_import_disabled")
            if not downloader_command(jobs.settings):
                raise APIError(409, "未安装 yt-dlp，无法导入链接", code="link_import_unavailable")
            params["limit_bytes"] = min(
                params["limit_bytes"], jobs.settings.link_import_max_mb * MIB
            )
            params["timeout_minutes"] = min(
                params["timeout_minutes"], jobs.settings.link_import_timeout_minutes
            )
        for field in ("encoding", "preview_encoding", "playable_encoding", "name"):
            params.pop(field, None)
        if "requested_edit" in params:
            params["edit"] = params.pop("requested_edit")
        params["storage_bytes"] = job_bytes(db, old.kind, params, list(old.video_ids))
        params["storage_plan"] = job_requirements(
            jobs.settings, old.kind, params, params["storage_bytes"]
        )
        if params["storage_bytes"]:
            check_budget(
                db, jobs.settings, params["storage_bytes"], requirements=params["storage_plan"]
            )
        # Persist the complete retry before waking a worker; keep the failed record.
        new = Job(
            kind=old.kind,
            params=params,
            video_ids=list(old.video_ids),
            priority=old.priority,
            retry_of=old.id,
            message="重试排队中",
        )
        db.add(new)
        db.commit()
        jobs.enqueue(new.id)
        jobs.publish(new)
        return jobs.describe(db, new)


@router.get("/jobs/{job_id}/download")
def job_download(
    job_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> FileResponse:
    job = _get(db, job_id)
    if not job.result_file:
        raise APIError(
            status.HTTP_404_NOT_FOUND, "该任务没有可下载的文件", code="job_download_unavailable"
        )
    path = abs_path(settings, job.result_file)
    if not path.exists():
        raise APIError(status.HTTP_404_NOT_FOUND, "文件已被清理", code="job_file_removed")
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
