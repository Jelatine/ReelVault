from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import Settings, StorageRoot
from ..db import get_db
from ..errors import APIError
from ..locations import LocationUnavailable, library_root, register_root
from ..models import Job, RuntimeSetting, Upload, Video
from ..storage import lock_budget
from .deps import get_settings

router = APIRouter(
    prefix="/api/system/locations", tags=["locations"], dependencies=[Depends(require_auth)]
)


def load_locations(db: Session, settings: Settings) -> Settings:
    saved = db.get(RuntimeSetting, "storage_locations")
    if saved is None:
        return settings.model_copy(deep=True)
    validated = Settings(**saved.value)
    return settings.model_copy(
        update={
            "storage_locations": validated.storage_locations,
            "storage_default": validated.storage_default,
        },
        deep=True,
    )


def save_locations(db: Session, original: Settings, updated: Settings) -> None:
    values = {
        "storage_locations": {
            key: root.model_dump(mode="json") for key, root in updated.storage_locations.items()
        },
        "storage_default": updated.storage_default,
    }
    saved = db.get(RuntimeSetting, "storage_locations")
    if saved is None:
        db.add(RuntimeSetting(key="storage_locations", value=values))
    else:
        saved.value = values
    db.commit()
    original.storage_locations = updated.storage_locations
    original.storage_default = updated.storage_default


def describe_locations(db: Session, settings: Settings) -> dict[str, Any]:
    locations = []
    for key, name, path in [
        ("local", "主存储", settings.library_dir),
        *((key, entry.name, entry.path) for key, entry in settings.storage_locations.items()),
    ]:
        prefix = "library/" if key == "local" else f"volumes/{key}/"
        count = (
            db.scalar(
                select(func.count()).select_from(Video).where(Video.file_path.startswith(prefix))
            )
            or 0
        )
        entry: dict[str, Any] = {
            "id": key,
            "name": name,
            "path": str(path),
            "video_count": count,
            "available": False,
            "total": None,
            "free": None,
            "used": None,
        }
        try:
            root = library_root(settings, key)
            usage = shutil.disk_usage(root)
            entry.update(available=True, total=usage.total, free=usage.free, used=usage.used)
        except OSError:
            pass
        locations.append(entry)
    return {"default_id": settings.storage_default, "items": locations}


@router.get("")
def list_locations(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    return describe_locations(db, settings)


class NewLocation(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=4096)


@router.post("")
def add_location(
    body: NewLocation, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    lock_budget(db)
    updated = load_locations(db, settings)
    if len(updated.storage_locations) >= 32:
        raise APIError(400, "最多支持 32 个附加存储位置", code="storage_location_limit")
    try:
        key, root = register_root(updated, Path(body.path), body.name)
    except (OSError, ValueError) as error:
        raise APIError(400, str(error), code="storage_location_invalid") from error
    updated.storage_locations[key] = root
    save_locations(db, settings, updated)
    return describe_locations(db, settings)


@router.patch("/{location_id}")
def update_location(
    location_id: str,
    body: NewLocation,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    lock_budget(db)
    updated = load_locations(db, settings)
    if location_id not in updated.storage_locations:
        raise APIError(404, "存储位置不存在", code="storage_location_not_found")
    if not body.name.strip():
        raise APIError(400, "存储位置名称无效", code="storage_location_invalid")
    candidate = Path(body.path)
    if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_dir():
        raise APIError(400, "请选择已存在的绝对目录", code="storage_location_invalid")
    candidate = candidate.resolve(strict=True)
    # Check overlap without creating or reinitializing any directories.
    others = [
        updated.data_dir.resolve(),
        *(root.path for key, root in updated.storage_locations.items() if key != location_id),
    ]
    if any(
        candidate == root or candidate.is_relative_to(root) or root.is_relative_to(candidate)
        for root in others
    ):
        raise APIError(400, "存储目录不能重叠或包含主数据目录", code="storage_location_invalid")
    updated.storage_locations[location_id] = StorageRoot(name=body.name.strip(), path=candidate)
    try:
        library_root(updated, location_id)
    except LocationUnavailable as error:
        raise APIError(
            400, "挂载目录的存储标识不匹配，未改变配置", code="storage_location_identity_mismatch"
        ) from error
    save_locations(db, settings, updated)
    return describe_locations(db, settings)


class DefaultLocation(BaseModel):
    location_id: str


@router.put("/default")
def set_default(
    body: DefaultLocation, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    lock_budget(db)
    updated = load_locations(db, settings)
    library_root(updated, body.location_id)
    updated.storage_default = body.location_id
    save_locations(db, settings, updated)
    return describe_locations(db, settings)


@router.delete("/{location_id}")
def remove_location(
    location_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    lock_budget(db)
    updated = load_locations(db, settings)
    if location_id not in updated.storage_locations:
        raise APIError(404, "存储位置不存在", code="storage_location_not_found")
    prefix = f"volumes/{location_id}/"
    if (
        db.scalar(select(Video.id).where(Video.file_path.startswith(prefix)).limit(1))
        or db.scalar(select(Upload.id).where(Upload.storage_id == location_id).limit(1))
        or any(
            job.params.get("output", {}).get("storage_id") == location_id
            or job.params.get("storage_id") == location_id
            for job in db.scalars(
                select(Job).where(Job.status.in_(["queued", "running", "paused"]))
            )
        )
    ):
        raise APIError(
            409, "存储位置仍有视频或未完成任务，不能移除", code="storage_location_in_use"
        )
    del updated.storage_locations[location_id]
    if updated.storage_default == location_id:
        updated.storage_default = "local"
    save_locations(db, settings, updated)
    # Keep the marker and directory: no media or user files are deleted.
    return describe_locations(db, settings)
