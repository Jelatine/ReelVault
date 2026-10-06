from __future__ import annotations

import asyncio
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import Settings
from ..db import get_db
from ..library import abs_path, rel_path
from ..media.assets import references
from ..media.ffmpeg import FFmpegError, run_command
from ..models import EditPreset, Job, MediaAsset, Video, new_id
from .deps import get_settings

router = APIRouter(
    prefix="/api/audio-assets", tags=["assets"], dependencies=[Depends(require_auth)]
)
EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
    ".opus",
    ".aif",
    ".aiff",
    ".wma",
    ".webm",
}


def asset_dict(asset: MediaAsset) -> dict[str, Any]:
    return {"id": asset.id, "name": asset.name, "size": asset.size, "duration": asset.duration}


@router.get("")
def list_audio(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        asset_dict(a)
        for a in db.scalars(
            select(MediaAsset)
            .where(MediaAsset.kind == "audio")
            .order_by(MediaAsset.created_at.desc())
        )
    ]


@router.post("")
async def upload_audio(
    file: UploadFile,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    name = Path((file.filename or "").replace("\\", "/")).name[:255]
    ext = Path(name).suffix.lower()
    if ext not in EXTENSIONS:
        raise HTTPException(400, "请选择 MP3、WAV、M4A、AAC、FLAC、OGG 等音频文件")
    limit = settings.audio_upload_max_mb * 1024 * 1024
    if file.size is not None and file.size > limit:
        raise HTTPException(413, f"音频文件不能超过 {settings.audio_upload_max_mb} MiB")
    asset_id = new_id()
    temporary = settings.tmp_dir / f"audio-{asset_id}{ext}"
    target = settings.assets_dir / f"{asset_id}{ext}"
    size, digest = 0, hashlib.sha256()
    committed = False
    try:
        async with aiofiles.open(temporary, "wb") as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"音频文件不能超过 {settings.audio_upload_max_mb} MiB")
                digest.update(chunk)
                await stream.write(chunk)
        if not size:
            raise HTTPException(400, "音频文件为空")
        result = await asyncio.wait_for(
            run_command(
                [
                    settings.ffprobe,
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-format_whitelist",
                    "mp3,wav,mov,aac,flac,ogg,aiff,asf,matroska,webm",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(temporary),
                ]
            ),
            30,
        )
        data = json.loads(result.stdout)
        audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
        if audio is None:
            raise HTTPException(400, "文件中没有音轨")
        duration = float((data.get("format") or {}).get("duration") or audio.get("duration") or 0)
        if not math.isfinite(duration) or duration <= 0:
            raise HTTPException(400, "无法获取音频时长")
        temporary.replace(target)
        asset = MediaAsset(
            id=asset_id,
            kind="audio",
            name=name,
            file_path=rel_path(settings, target),
            size=size,
            sha256=digest.hexdigest(),
            duration=duration,
            stream_index=int(audio["index"]),
        )
        db.add(asset)
        db.commit()
        committed = True
        return asset_dict(asset)
    except (FFmpegError, ValueError, TimeoutError) as error:
        raise HTTPException(400, "音频文件无法读取") from error
    finally:
        await file.close()
        temporary.unlink(missing_ok=True)
        if not committed:
            target.unlink(missing_ok=True)


def get_asset(db: Session, asset_id: str) -> MediaAsset:
    asset = db.get(MediaAsset, asset_id)
    if asset is None or asset.kind != "audio":
        raise HTTPException(404, "音频素材不存在")
    return asset


@router.get("/{asset_id}/stream")
def stream_audio(
    asset_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> FileResponse:
    asset = get_asset(db, asset_id)
    path = abs_path(settings, asset.file_path)
    if not path.is_file():
        raise HTTPException(404, "音频素材文件缺失")
    return FileResponse(path, filename=asset.name, content_disposition_type="inline")


@router.delete("/{asset_id}")
async def delete_audio(
    asset_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, bool]:
    asset = get_asset(db, asset_id)
    for field in (Job.params, Video.edit_params, EditPreset.edit):
        if any(references(value, asset_id) for value in db.scalars(select(field))):
            raise HTTPException(409, "素材被任务、编辑历史或预设引用，不能删除")
    path = abs_path(settings, asset.file_path)
    db.delete(asset)
    db.commit()
    path.unlink(missing_ok=True)
    return {"ok": True}
