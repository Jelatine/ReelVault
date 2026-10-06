from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import Settings
from ..db import get_db
from ..library import abs_path, rel_path
from ..media.assets import AssetError, checked_asset, references
from ..media.ffmpeg import FFmpegError, ffmpeg_args, run_command
from ..media.watermark import FONT
from ..models import EditPreset, Job, MediaAsset, Video, new_id
from .deps import get_settings

router = APIRouter(
    prefix="/api/image-assets", tags=["assets"], dependencies=[Depends(require_auth)]
)
FORMATS = {".png": "png_pipe", ".jpg": "jpeg_pipe", ".jpeg": "jpeg_pipe", ".webp": "webp_pipe"}


def asset_dict(asset: MediaAsset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "name": asset.name,
        "size": asset.size,
        "url": f"/api/image-assets/{asset.id}/stream",
    }


@router.get("")
def list_images(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        asset_dict(a)
        for a in db.scalars(
            select(MediaAsset)
            .where(MediaAsset.kind == "image")
            .order_by(MediaAsset.created_at.desc())
        )
    ]


@router.get("/font")
def bundled_font() -> FileResponse:
    if not FONT.is_file():
        raise HTTPException(404, "内置中文字体缺失")
    return FileResponse(
        FONT, media_type="font/otf", headers={"Cache-Control": "private, max-age=86400"}
    )


@router.post("")
async def upload_image(
    file: UploadFile, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    name = Path((file.filename or "").replace("\\", "/")).name[:255]
    ext = Path(name).suffix.lower()
    asset_id = new_id()
    temporary = settings.tmp_dir / f"image-{asset_id}{ext}"
    target = settings.assets_dir / f"{asset_id}.png"
    committed = False
    try:
        if ext not in FORMATS:
            raise HTTPException(400, "请选择 PNG、JPEG 或静态 WebP 图片")
        data = await file.read(10 * 1024 * 1024 + 1)
        if not data:
            raise HTTPException(400, "图片文件为空")
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(413, "图片不能超过 10 MiB")
        temporary.write_bytes(data)
        result = await asyncio.wait_for(
            run_command(
                [
                    settings.ffprobe,
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-f",
                    FORMATS[ext],
                    "-show_streams",
                    "-of",
                    "json",
                    str(temporary),
                ]
            ),
            30,
        )
        streams = json.loads(result.stdout).get("streams", [])
        stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        if not stream:
            raise HTTPException(400, "图片无法读取")
        width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
        if min(width, height) <= 0:
            raise HTTPException(400, "图片无法读取，请使用静态 PNG、JPEG 或 WebP")
        if max(width, height) > 4096 or width * height > 16_777_216:
            raise HTTPException(400, "图片尺寸不能超过 4096×4096")
        await asyncio.wait_for(
            run_command(
                ffmpeg_args(
                    settings.ffmpeg,
                    [
                        "-protocol_whitelist",
                        "file,pipe",
                        "-f",
                        FORMATS[ext],
                        "-i",
                        str(temporary),
                        "-frames:v",
                        "1",
                        "-c:v",
                        "png",
                        "-pix_fmt",
                        "rgba",
                        str(target),
                    ],
                    progress=False,
                )
            ),
            30,
        )
        normalized = target.read_bytes()
        asset = MediaAsset(
            id=asset_id,
            kind="image",
            name=name,
            file_path=rel_path(settings, target),
            size=len(normalized),
            sha256=hashlib.sha256(normalized).hexdigest(),
            duration=0,
            stream_index=0,
        )
        db.add(asset)
        db.commit()
        committed = True
        return asset_dict(asset)
    except (FFmpegError, ValueError, TimeoutError) as error:
        raise HTTPException(400, "图片无法解码") from error
    finally:
        await file.close()
        temporary.unlink(missing_ok=True)
        if not committed:
            target.unlink(missing_ok=True)


@router.get("/{asset_id}/stream")
def stream_image(
    asset_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> FileResponse:
    try:
        asset = checked_asset(db, settings, asset_id, "image")
    except AssetError as error:
        raise HTTPException(404, str(error)) from error
    return FileResponse(
        abs_path(settings, asset.file_path),
        media_type="image/png",
        filename=asset.name,
        content_disposition_type="inline",
    )


@router.delete("/{asset_id}")
def delete_image(
    asset_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, bool]:
    asset = db.get(MediaAsset, asset_id)
    if asset is None or asset.kind != "image":
        raise HTTPException(404, "图片素材不存在")
    for field in (Job.params, Video.edit_params, EditPreset.edit):
        if any(references(value, asset_id) for value in db.scalars(select(field))):
            raise HTTPException(409, "图片被任务、编辑历史或预设引用，不能删除")
    path = abs_path(settings, asset.file_path)
    db.delete(asset)
    db.commit()
    path.unlink(missing_ok=True)
    return {"ok": True}
