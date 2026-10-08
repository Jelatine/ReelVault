from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import Settings
from ..db import get_db
from ..errors import APIError
from ..jobs.manager import JobManager, job_to_dict
from ..library import abs_path
from ..media import ops
from ..media.assets import AssetError, audio_asset, image_asset, lut_asset, subtitle_asset
from ..models import Video
from .deps import get_jobs, get_settings
from .videos import get_video

router = APIRouter(prefix="/api/videos", tags=["history"], dependencies=[Depends(require_auth)])


def sources_of(video: Video) -> list[dict[str, Any]]:
    if video.edit_sources:
        return video.edit_sources
    params = video.edit_params or {}
    ids = params.get("video_ids") or ([video.source_video_id] if video.source_video_id else [])
    return [{"id": source_id, "title": source_id} for source_id in ids]


def source_state(source: dict[str, Any], db: Session, settings: Settings) -> dict[str, Any]:
    video = db.get(Video, source["id"])
    if source.get("file_path") and (video is None or video.file_path != source["file_path"]):
        saved = db.scalar(
            select(Video).where(
                Video.file_path == source["file_path"], Video.size == source.get("size")
            )
        )
        if saved is not None:
            video = saved
    reason = None
    if video is None:
        reason = "源视频已彻底删除"
    elif not source.get("file_path"):
        reason = "旧记录未保存源文件版本"
    elif video.file_path != source["file_path"] or video.size != source.get("size"):
        reason = "源文件版本已变化"
    elif video.status != "ready":
        reason = "源视频尚未就绪"
    elif (
        video.file_path not in settings.s3_objects
        and not abs_path(settings, video.file_path).is_file()
    ):
        # Archived S3 originals are fetched by the replayed job itself.
        reason = "源文件缺失"
    elif source.get("cover_sha256"):
        cover = settings.derived_dir / video.id / "poster.jpg"
        if (
            not cover.is_file()
            or hashlib.sha256(cover.read_bytes()).hexdigest() != source["cover_sha256"]
        ):
            reason = "源封面已变化或缺失"
    return {
        "id": video.id if video else source["id"],
        "title": source.get("title") or source["id"],
        "exists": video is not None,
        "deleted": bool(video and video.deleted_at),
        "available": reason is None,
        "reason": reason,
    }


@router.get("/{video_id}/history")
def history(
    video_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    first = get_video(db, video_id, allow_deleted=True)
    pending = [first]
    seen: set[str] = set()
    nodes = []
    while pending and len(seen) < 1000:
        video = pending.pop(0)
        if video.id in seen:
            continue
        seen.add(video.id)
        sources = [source_state(source, db, settings) for source in sources_of(video)]
        asset_error = None
        if (video.edit_params or {}).get("op") == "adjust":
            try:
                lut_asset(db, settings, ops.AdjustParams.model_validate(video.edit_params))
            except (AssetError, ValueError) as error:
                asset_error = str(error)
        if (video.edit_params or {}).get("op") == "watermark":
            try:
                image_asset(db, settings, ops.WatermarkParams.model_validate(video.edit_params))
            except (AssetError, ValueError) as error:
                asset_error = str(error)
        if (video.edit_params or {}).get("op") == "subtitle":
            try:
                subtitle_asset(db, settings, ops.SubtitleParams.model_validate(video.edit_params))
            except (AssetError, ValueError) as error:
                asset_error = str(error)
        if (video.edit_params or {}).get("op") == "audio":
            try:
                audio_asset(db, settings, ops.AudioParams.model_validate(video.edit_params))
            except (AssetError, ValueError) as error:
                asset_error = str(error)
        nodes.append(
            {
                "id": video.id,
                "title": video.title,
                "deleted": video.deleted_at is not None,
                "edit": video.edit_params,
                "asset_error": asset_error,
                "sources": sources,
                "can_recreate": bool(
                    video.edit_params
                    and sources
                    and all(s["available"] for s in sources)
                    and not asset_error
                ),
            }
        )
        for source in sources:
            parent = db.get(Video, source["id"])
            if parent and parent.id not in seen:
                pending.append(parent)
    return {"nodes": nodes, "truncated": bool(pending)}


@router.post("/{video_id}/recreate")
def recreate(
    video_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    video = get_video(db, video_id, allow_deleted=True)
    sources = sources_of(video)
    if not video.edit_params or not sources:
        raise APIError(400, "该视频没有可重新生成的编辑记录", code="history_missing")
    resolved = []
    for source in sources:
        state = source_state(source, db, settings)
        if not state["available"]:
            raise APIError(
                409,
                f"{state['title']}：{state['reason']}",
                code="history_source_unavailable",
                params={"title": state["title"]},
            )
        resolved.append({**source, "id": state["id"]})
    edit: ops.EditParams = TypeAdapter(ops.EditParams).validate_python(video.edit_params)
    audio_sha256 = None
    subtitle_sha256 = None
    image_sha256 = None
    lut_sha256 = None
    if isinstance(edit, ops.AdjustParams):
        try:
            asset = lut_asset(db, settings, edit)
            lut_sha256 = asset.sha256 if asset else None
        except AssetError as error:
            raise APIError(409, str(error), code="asset_invalid") from error
    if isinstance(edit, ops.WatermarkParams):
        try:
            asset = image_asset(db, settings, edit)
            image_sha256 = asset.sha256 if asset else None
        except AssetError as error:
            raise APIError(409, str(error), code="asset_invalid") from error
    if isinstance(edit, ops.SubtitleParams):
        try:
            asset = subtitle_asset(db, settings, edit)
            subtitle_sha256 = asset.sha256 if asset else None
        except AssetError as error:
            raise APIError(409, str(error), code="asset_invalid") from error
    if isinstance(edit, ops.AudioParams):
        try:
            asset = audio_asset(db, settings, edit)
            audio_sha256 = asset.sha256 if asset else None
        except AssetError as error:
            raise APIError(409, str(error), code="asset_invalid") from error
    ids = [source["id"] for source in resolved]
    if isinstance(edit, (ops.MergeParams, ops.CompositeParams)):
        edit.video_ids = ids
    params = {
        "edit": edit.model_dump(),
        "audio_sha256": audio_sha256,
        "subtitle_sha256": subtitle_sha256,
        "image_sha256": image_sha256,
        "lut_sha256": lut_sha256,
        "output": {"mode": "new", "title": f"{video.title} (重新生成)"[:255]},
        "history_replay": True,
        "source_versions": {s["id"]: s["file_path"] for s in resolved},
        "source_covers": {s["id"]: s["cover_sha256"] for s in resolved if s.get("cover_sha256")},
    }
    return job_to_dict(jobs.submit(db, "edit", params, ids))
