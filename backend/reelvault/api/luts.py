from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import Settings
from ..db import get_db
from ..errors import APIError
from ..library import abs_path, rel_path
from ..media.assets import references
from ..media.ffmpeg import FFmpegError, ffmpeg_args, run_command
from ..media.luts import parse_cube
from ..models import EditPreset, Job, MediaAsset, Video, new_id
from .deps import get_settings

router = APIRouter(prefix="/api/lut-assets", tags=["assets"], dependencies=[Depends(require_auth)])


def asset_dict(asset: MediaAsset) -> dict[str, Any]:
    return {"id": asset.id, "name": asset.name, "size": asset.size, "meta": asset.meta}


@router.get("")
def list_luts(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        asset_dict(a)
        for a in db.scalars(
            select(MediaAsset)
            .where(MediaAsset.kind == "lut")
            .order_by(MediaAsset.created_at.desc())
        )
    ]


@router.post("")
async def upload_lut(
    file: UploadFile,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    name = Path((file.filename or "").replace("\\", "/")).name[:255]
    asset_id = new_id()
    temporary = settings.tmp_dir / f"lut-{asset_id}"
    target = settings.assets_dir / f"{asset_id}.cube"
    committed = False
    try:
        if Path(name).suffix.lower() != ".cube":
            raise APIError(400, "请选择 .cube 格式的 1D 或 3D LUT", code="lut_format_unsupported")
        data = await file.read(20 * 1024 * 1024 + 1)
        if len(data) > 20 * 1024 * 1024:
            raise APIError(413, "LUT 不能超过 20 MiB", code="lut_too_large")
        normalized, meta = parse_cube(data)
        temporary.mkdir()
        (temporary / "grade.cube").write_bytes(normalized)
        filt = (
            "lut3d=file=grade.cube:interp=tetrahedral"
            if meta["dimension"] == 3
            else "lut1d=file=grade.cube:interp=cubic"
        )
        # Reject files unsupported by this installation before exposing an asset.
        await asyncio.wait_for(
            run_command(
                ffmpeg_args(
                    settings.ffmpeg,
                    [
                        "-f",
                        "lavfi",
                        "-i",
                        "color=size=16x16:duration=0.04",
                        "-vf",
                        filt,
                        "-frames:v",
                        "1",
                        "-f",
                        "null",
                        "-",
                    ],
                    progress=False,
                ),
                cwd=temporary,
            ),
            30,
        )
        target.write_bytes(normalized)
        asset = MediaAsset(
            id=asset_id,
            kind="lut",
            name=name,
            file_path=rel_path(settings, target),
            sha256=hashlib.sha256(normalized).hexdigest(),
            size=len(normalized),
            meta=meta,
        )
        db.add(asset)
        db.commit()
        committed = True
        return asset_dict(asset)
    except ValueError as error:
        raise APIError(400, str(error), code="lut_invalid") from error
    except (FFmpegError, TimeoutError) as error:
        raise APIError(400, "当前 FFmpeg 无法读取该 LUT", code="lut_ffmpeg_unreadable") from error
    finally:
        await file.close()
        (temporary / "grade.cube").unlink(missing_ok=True)
        if temporary.exists():
            temporary.rmdir()
        if not committed:
            target.unlink(missing_ok=True)


@router.delete("/{asset_id}")
def delete_lut(
    asset_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, bool]:
    asset = db.get(MediaAsset, asset_id)
    if asset is None or asset.kind != "lut":
        raise APIError(404, "LUT 素材不存在", code="lut_asset_not_found")
    for field in (Job.params, Video.edit_params, EditPreset.edit):
        if any(references(value, asset_id) for value in db.scalars(select(field))):
            raise APIError(409, "LUT 被任务、编辑历史或预设引用，不能删除", code="asset_in_use")
    path = abs_path(settings, asset.file_path)
    db.delete(asset)
    db.commit()
    path.unlink(missing_ok=True)
    return {"ok": True}
