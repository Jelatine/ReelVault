from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import get_db
from ..errors import APIError
from ..models import Folder, Video

router = APIRouter(prefix="/api/folders", tags=["folders"], dependencies=[Depends(require_auth)])


class FolderBody(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: int | None = None


class FolderPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    parent_id: int | None = None
    move: bool = False


def _get(db: Session, folder_id: int) -> Folder:
    folder = db.get(Folder, folder_id)
    if folder is None:
        raise APIError(status.HTTP_404_NOT_FOUND, "文件夹不存在", code="folder_not_found")
    return folder


def _check_name(db: Session, name: str, parent_id: int | None, exclude: int | None = None) -> None:
    stmt = select(Folder).where(Folder.name == name)
    stmt = stmt.where(
        Folder.parent_id.is_(None) if parent_id is None else Folder.parent_id == parent_id
    )
    existing = db.scalar(stmt)
    if existing is not None and existing.id != exclude:
        raise APIError(status.HTTP_409_CONFLICT, "同名文件夹已存在", code="folder_name_conflict")


def _dict(f: Folder, counts: dict[int | None, int]) -> dict[str, Any]:
    return {"id": f.id, "name": f.name, "parent_id": f.parent_id, "count": counts.get(f.id, 0)}


@router.get("")
def list_folders(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    counts: dict[int | None, int] = dict(
        db.execute(  # type: ignore[arg-type]
            select(Video.folder_id, func.count())
            .where(Video.deleted_at.is_(None))
            .group_by(Video.folder_id)
        ).all()
    )
    folders = db.scalars(select(Folder).order_by(Folder.name)).all()
    return [_dict(f, counts) for f in folders]


@router.post("")
def create_folder(body: FolderBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    name = body.name.strip()
    if body.parent_id is not None:
        _get(db, body.parent_id)
    _check_name(db, name, body.parent_id)
    folder = Folder(name=name, parent_id=body.parent_id)
    db.add(folder)
    db.commit()
    return _dict(folder, {})


@router.patch("/{folder_id}")
def update_folder(
    folder_id: int, body: FolderPatch, db: Session = Depends(get_db)
) -> dict[str, Any]:
    folder = _get(db, folder_id)
    parent_id = folder.parent_id
    if body.move:
        # Reject moving a folder into itself or one of its descendants.
        cursor = body.parent_id
        while cursor is not None:
            if cursor == folder.id:
                raise APIError(
                    status.HTTP_400_BAD_REQUEST, "不能移动到自身或子文件夹", code="folder_cycle"
                )
            cursor = _get(db, cursor).parent_id
        parent_id = body.parent_id
    name = body.name.strip() if body.name else folder.name
    _check_name(db, name, parent_id, exclude=folder.id)
    folder.name = name
    folder.parent_id = parent_id
    db.commit()
    return _dict(folder, {})


@router.delete("/{folder_id}")
def delete_folder(folder_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    """Delete a folder; its videos and sub-folders move up to the parent."""
    folder = _get(db, folder_id)
    for child in db.scalars(select(Folder).where(Folder.parent_id == folder.id)):
        child.parent_id = folder.parent_id
    for video in db.scalars(select(Video).where(Video.folder_id == folder.id)):
        video.folder_id = folder.parent_id
    db.delete(folder)
    db.commit()
    return {"ok": True}
