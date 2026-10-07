from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import Settings
from ..db import get_db
from ..errors import APIError
from ..jobs.manager import JobManager
from ..link_download import downloader_command
from ..link_network import addresses, parse_url
from ..locations import library_root
from ..models import Folder, RuntimeSetting
from ..storage import MIB, lock_budget
from .deps import get_jobs, get_settings

router = APIRouter(tags=["links"], dependencies=[Depends(require_auth)])


class LinkPreferences(BaseModel):
    link_import_enabled: bool = Field(strict=True)
    link_import_max_mb: int = Field(1024, ge=16, le=102400, strict=True)
    link_import_timeout_minutes: int = Field(30, ge=1, le=1440, strict=True)


class LinkBody(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    title: str = Field("", max_length=255)
    folder_id: int | None = Field(None, gt=0)
    storage_id: str | None = Field(None, pattern=r"^(local|[a-f0-9]{32})$")
    acknowledge_rights: bool = Field(False, strict=True)


def link_status(settings: Settings) -> dict[str, Any]:
    return {
        "enabled": settings.link_import_enabled,
        "available": downloader_command(settings) is not None,
        "max_mb": settings.link_import_max_mb,
        "timeout_minutes": settings.link_import_timeout_minutes,
        "private_sources_allowed": settings.link_import_allow_private,
    }


@router.get("/api/system/link-import")
def status(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return link_status(settings)


@router.put("/api/system/link-import")
def preferences(
    body: LinkPreferences,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if body.link_import_enabled and not downloader_command(settings):
        raise APIError(409, "未安装 yt-dlp，无法启用链接导入", code="link_import_unavailable")
    saved = db.get(RuntimeSetting, "link_import")
    values = body.model_dump()
    if saved:
        saved.value = values
    else:
        db.add(RuntimeSetting(key="link_import", value=values))
    db.commit()
    for key, value in values.items():
        setattr(settings, key, value)
    return link_status(settings)


@router.post("/api/import-links")
async def import_link(
    body: LinkBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    if not settings.link_import_enabled:
        raise APIError(403, "链接导入尚未启用", code="link_import_disabled")
    if not downloader_command(settings):
        raise APIError(409, "未安装 yt-dlp，无法导入链接", code="link_import_unavailable")
    if not body.acknowledge_rights:
        raise APIError(
            400, "请确认有权下载并遵守版权与站点条款", code="link_import_rights_required"
        )
    url = body.url.strip()
    try:
        host, port = parse_url(url)
        await addresses(host, port, allow_private=settings.link_import_allow_private)
    except ValueError as error:
        raise APIError(400, str(error), code="link_import_url_invalid") from error
    lock_budget(db)
    storage_id = body.storage_id or settings.storage_default
    library_root(settings, storage_id)
    if body.folder_id is not None and db.get(Folder, body.folder_id) is None:
        raise APIError(404, "文件夹不存在", code="folder_not_found")
    job = jobs.submit(
        db,
        "link_import",
        {
            "url": url,
            "title": body.title.strip(),
            "folder_id": body.folder_id,
            "storage_id": storage_id,
            "limit_bytes": settings.link_import_max_mb * MIB,
            "timeout_minutes": settings.link_import_timeout_minutes,
            "acknowledge_rights": True,
        },
        [],
    )
    return jobs.describe(db, job)
