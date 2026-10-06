from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import Settings
from ..db import get_db
from ..library import abs_path, rel_path
from ..media.assets import AssetError, checked_asset, references
from ..media.ffmpeg import FFmpegError, ffprobe_json
from ..media.probe import subtitle_streams
from ..media.subtitles import FORMATS, to_vtt
from ..models import EditPreset, Job, MediaAsset, SubtitleTrack, Video, new_id
from .deps import get_settings
from .videos import get_video

router = APIRouter(prefix="/api", tags=["subtitles"], dependencies=[Depends(require_auth)])


def asset_dict(asset: MediaAsset) -> dict[str, Any]:
    return {"id": asset.id, "name": asset.name, "size": asset.size}


@router.get("/subtitle-assets")
def list_assets(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        asset_dict(asset)
        for asset in db.scalars(
            select(MediaAsset)
            .where(MediaAsset.kind == "subtitle")
            .order_by(MediaAsset.created_at.desc())
        )
    ]


@router.post("/subtitle-assets")
async def upload_subtitle(
    file: UploadFile,
    encoding: Literal["utf-8", "utf-16", "gb18030"] = Form("utf-8"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    name = Path((file.filename or "").replace("\\", "/")).name[:255]
    ext = Path(name).suffix.lower()
    if ext not in FORMATS:
        raise HTTPException(400, "请选择 SRT、ASS 或 VTT 字幕")
    data = await file.read(5 * 1024 * 1024 + 1)
    await file.close()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "字幕不能超过 5 MiB")
    try:
        normalized = data.decode("utf-8-sig" if encoding == "utf-8" else encoding).encode("utf-8")
    except UnicodeError as error:
        raise HTTPException(400, "字幕编码不匹配，请选择正确编码") from error
    asset_id = new_id()
    path = settings.assets_dir / f"{asset_id}{ext}"
    cache = settings.assets_dir / f"{asset_id}.webvtt"
    committed = False
    try:
        path.write_bytes(normalized)
        converted = await to_vtt(settings.ffmpeg, path)
        cache.write_bytes(converted)
        asset = MediaAsset(
            id=asset_id,
            kind="subtitle",
            name=name,
            file_path=rel_path(settings, path),
            sha256=hashlib.sha256(normalized).hexdigest(),
            size=len(normalized),
            duration=0,
            stream_index=0,
        )
        db.add(asset)
        db.commit()
        committed = True
        return asset_dict(asset)
    except (FFmpegError, TimeoutError) as error:
        raise HTTPException(400, "字幕没有有效片段或无法解析") from error
    finally:
        if not committed:
            path.unlink(missing_ok=True)
            cache.unlink(missing_ok=True)


@router.get("/subtitle-assets/{asset_id}/vtt")
async def asset_vtt(
    asset_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> Response:
    try:
        asset = checked_asset(db, settings, asset_id, "subtitle")
    except AssetError as error:
        raise HTTPException(404, str(error)) from error
    cache = settings.assets_dir / f"{asset.id}.webvtt"
    if cache.is_file():
        data = cache.read_bytes()
    else:
        try:
            data = await to_vtt(settings.ffmpeg, abs_path(settings, asset.file_path))
        except (FFmpegError, TimeoutError) as error:
            raise HTTPException(400, "字幕无法转换，请检查文件与 FFmpeg") from error
        cache.write_bytes(data)
    return Response(data, media_type="text/vtt", headers={"Cache-Control": "private, max-age=3600"})


class TrackBody(BaseModel):
    asset_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    label: str = Field(min_length=1, max_length=128)
    language: str = Field("und", pattern=r"^[a-zA-Z]{2,8}(?:-[a-zA-Z0-9]{1,8})*$", max_length=35)


@router.post("/videos/{video_id}/subtitles")
async def attach(
    video_id: str,
    body: TrackBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    get_video(db, video_id)
    try:
        checked_asset(db, settings, body.asset_id, "subtitle")
    except AssetError as error:
        raise HTTPException(400, str(error)) from error
    if db.scalar(
        select(SubtitleTrack.id).where(
            SubtitleTrack.video_id == video_id, SubtitleTrack.asset_id == body.asset_id
        )
    ):
        raise HTTPException(409, "这份字幕已添加到视频")
    track = SubtitleTrack(
        video_id=video_id,
        asset_id=body.asset_id,
        label=body.label.strip() or "字幕",
        language=body.language,
    )
    db.add(track)
    db.commit()
    return {"id": track.id}


@router.delete("/videos/{video_id}/subtitles/{track_id}")
async def detach(video_id: str, track_id: str, db: Session = Depends(get_db)) -> dict[str, bool]:
    get_video(db, video_id, allow_deleted=True)
    track = db.get(SubtitleTrack, track_id)
    if track is None or track.video_id != video_id:
        raise HTTPException(404, "字幕轨道不存在")
    db.delete(track)
    db.commit()
    return {"ok": True}


async def embedded_tracks(db: Session, settings: Settings, video: Video) -> list[dict[str, Any]]:
    if "subtitle_streams" not in (video.meta or {}):
        source, version = video.file_path, video.asset_version
        try:
            data = await asyncio.wait_for(
                ffprobe_json(settings.ffprobe, str(abs_path(settings, source))), 30
            )
        except (FFmpegError, TimeoutError) as error:
            raise HTTPException(400, "无法读取视频字幕轨道") from error
        db.refresh(video)
        if video.file_path != source or video.asset_version != version:
            raise HTTPException(409, "视频版本已变化，请刷新")
        video.meta = {**(video.meta or {}), "subtitle_streams": subtitle_streams(data)}
        db.commit()
    return list(video.meta["subtitle_streams"])


@router.get("/videos/{video_id}/subtitles")
async def tracks(
    video_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> list[dict[str, Any]]:
    video = get_video(db, video_id, allow_deleted=True)
    result = [
        {
            "id": t.id,
            "label": t.label,
            "language": t.language,
            "asset_id": t.asset_id,
            "embedded_index": None,
            "playable": True,
            "codec": "external",
            "url": f"/api/subtitle-assets/{t.asset_id}/vtt",
        }
        for t in db.scalars(select(SubtitleTrack).where(SubtitleTrack.video_id == video_id))
    ]
    for stream in await embedded_tracks(db, settings, video):
        result.append(
            {
                "id": f"embedded-{stream['index']}",
                "label": stream["label"],
                "language": stream["language"],
                "asset_id": None,
                "embedded_index": stream["index"],
                "codec": stream["codec"],
                "playable": stream["text"],
                "url": (
                    f"/api/videos/{video.id}/subtitles/embedded/{stream['index']}"
                    f"?v={video.asset_version}"
                )
                if stream["text"]
                else None,
            }
        )
    return result


@router.get("/videos/{video_id}/subtitles/embedded/{index}")
async def embedded_vtt(
    video_id: str,
    index: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    video = get_video(db, video_id, allow_deleted=True)
    streams = await embedded_tracks(db, settings, video)
    stream = next((s for s in streams if s["index"] == index), None)
    if stream is None:
        raise HTTPException(404, "内封字幕轨道不存在")
    if not stream["text"]:
        raise HTTPException(400, "图像字幕无法转换为文本，可选择烧录")
    try:
        data = await to_vtt(settings.ffmpeg, abs_path(settings, video.file_path), index)
    except (FFmpegError, TimeoutError) as error:
        raise HTTPException(400, "内封字幕无法转换，请检查文件与 FFmpeg") from error
    return Response(data, media_type="text/vtt", headers={"Cache-Control": "private, max-age=3600"})


@router.delete("/subtitle-assets/{asset_id}")
async def delete_asset(
    asset_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, bool]:
    asset = db.get(MediaAsset, asset_id)
    if asset is None or asset.kind != "subtitle":
        raise HTTPException(404, "字幕素材不存在")
    if db.scalar(select(SubtitleTrack.id).where(SubtitleTrack.asset_id == asset_id)):
        raise HTTPException(409, "字幕仍挂在视频上，请先移除轨道")
    for field in (Job.params, Video.edit_params, EditPreset.edit):
        if any(references(value, asset_id) for value in db.scalars(select(field))):
            raise HTTPException(409, "字幕被任务、历史或预设引用，不能删除")
    path = abs_path(settings, asset.file_path)
    db.delete(asset)
    db.commit()
    path.unlink(missing_ok=True)
    (settings.assets_dir / f"{asset_id}.webvtt").unlink(missing_ok=True)
    return {"ok": True}
