from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import CurrentAuth, require_auth
from ..db import get_db
from ..errors import APIError
from ..grouping import parse_group
from ..models import SmartFolder
from ..search_syntax import parse_search
from .deps import FiniteNumber
from .videos import SortKey, list_videos

router = APIRouter(prefix="/api/smart-folders", tags=["smart-folders"])
SQLITE_MAX = 2**63 - 1
FolderID = Annotated[int, Path(gt=0, le=SQLITE_MAX)]


class SavedFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Renaming a tag can expand an existing query; only user writes are bounded.
    q: str = ""
    folder: str = Field("all", pattern=r"^(all|root|[1-9][0-9]{0,18})$")
    tag: str | None = Field(None, max_length=64)
    rating_min: int = Field(0, ge=0, le=5)
    favorite: bool | None = None
    duration_min: FiniteNumber | None = Field(None, ge=0)
    duration_max: FiniteNumber | None = Field(None, ge=0)
    size_min: int | None = Field(None, ge=0, le=SQLITE_MAX)
    size_max: int | None = Field(None, ge=0, le=SQLITE_MAX)
    resolution: Literal["4k", "1080p", "720p", "portrait", "landscape"] | None = None
    codec: str | None = Field(None, max_length=32)
    format: str | None = Field(None, max_length=64)
    created_after: datetime | None = None
    created_before: datetime | None = None
    captured_after: datetime | None = None
    captured_before: datetime | None = None
    include_children: bool = False
    auto: str | None = Field(None, max_length=80)
    sort: SortKey = "relevance"
    order: Literal["asc", "desc"] = "desc"

    @field_validator("folder")
    @classmethod
    def valid_folder(cls, value: str) -> str:
        if value not in ("all", "root") and int(value) > SQLITE_MAX:
            raise ValueError("Folder id is out of range")
        return value

    @field_validator("created_after", "created_before", "captured_after", "captured_before")
    @classmethod
    def utc_date(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def valid_ranges(self) -> SavedFilters:
        parse_search(self.q)
        if self.auto is not None:
            parse_group(self.auto)
        ranges: tuple[tuple[Any, Any], ...] = (
            (self.duration_min, self.duration_max),
            (self.size_min, self.size_max),
            (self.created_after, self.created_before),
            (self.captured_after, self.captured_before),
        )
        for low, high in ranges:
            if low is not None and high is not None and low > high:
                raise APIError(400, "筛选范围的下限不能超过上限", code="filter_range_invalid")
        return self


class FolderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    filters: SavedFilters

    @field_validator("filters")
    @classmethod
    def bounded_query(cls, value: SavedFilters) -> SavedFilters:
        if len(value.q) > 512:
            raise ValueError("Search query cannot exceed 512 characters")
        return value

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name cannot be empty")
        return value.strip()


class FolderPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, min_length=1, max_length=128)
    filters: SavedFilters | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Name cannot be null")
        return FolderBody.clean_name(value)

    @field_validator("filters")
    @classmethod
    def non_null_filters(cls, value: SavedFilters | None) -> SavedFilters:
        if value is None:
            raise ValueError("Filters cannot be null")
        return FolderBody.bounded_query(value)


def get_folder(db: Session, folder_id: int, auth: CurrentAuth) -> SmartFolder:
    folder = db.scalar(
        select(SmartFolder).where(SmartFolder.id == folder_id, SmartFolder.user_id == auth.user_id)
    )
    if folder is None:
        raise APIError(404, "智能文件夹不存在", code="smart_folder_not_found")
    return folder


def as_dict(folder: SmartFolder) -> dict[str, Any]:
    return {"id": folder.id, "name": folder.name, "filters": folder.filters}


def commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise APIError(409, "同名智能文件夹已存在", code="smart_folder_name_conflict") from error


@router.get("")
def list_folders(
    auth: CurrentAuth = Depends(require_auth), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return [
        as_dict(folder)
        for folder in db.scalars(
            select(SmartFolder)
            .where(SmartFolder.user_id == auth.user_id)
            .order_by(SmartFolder.name)
        )
    ]


@router.post("")
def create_folder(
    body: FolderBody, auth: CurrentAuth = Depends(require_auth), db: Session = Depends(get_db)
) -> dict[str, Any]:
    folder = SmartFolder(
        user_id=auth.user_id, name=body.name, filters=body.filters.model_dump(mode="json")
    )
    db.add(folder)
    commit(db)
    return as_dict(folder)


@router.patch("/{folder_id}")
def update_folder(
    folder_id: FolderID,
    body: FolderPatch,
    auth: CurrentAuth = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    folder = get_folder(db, folder_id, auth)
    if body.name is not None:
        folder.name = body.name
    if body.filters is not None:
        folder.filters = body.filters.model_dump(mode="json")
    commit(db)
    return as_dict(folder)


@router.delete("/{folder_id}")
def delete_folder(
    folder_id: FolderID, auth: CurrentAuth = Depends(require_auth), db: Session = Depends(get_db)
) -> dict[str, bool]:
    db.delete(get_folder(db, folder_id, auth))
    db.commit()
    return {"ok": True}


@router.get("/{folder_id}/videos")
def folder_videos(
    folder_id: FolderID,
    page: int = Query(1, ge=1),
    page_size: int = Query(48, ge=1, le=500),
    auth: CurrentAuth = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    filters = SavedFilters.model_validate(get_folder(db, folder_id, auth).filters)
    # One evaluator for normal and saved searches; never persist membership.
    return list_videos(**filters.model_dump(), trash=False, page=page, page_size=page_size, db=db)
