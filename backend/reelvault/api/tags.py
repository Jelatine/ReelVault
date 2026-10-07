from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, literal, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import get_db
from ..errors import APIError
from ..models import Tag, TagGroup, Upload, Video, video_tags

router = APIRouter(prefix="/api/tags", tags=["tags"], dependencies=[Depends(require_auth)])


class NamedBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be empty")
        return value


class TagBody(NamedBody):
    color: str | None = None
    group_id: int | None = Field(None, gt=0)

    @field_validator("color")
    @classmethod
    def clean_color(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("Use a six-digit hexadecimal color")
        return value.lower()


def lock(db: Session) -> None:
    # Serialize lookup + merge/rename/delete across requests and tabs.
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")


def commit(db: Session, *, group: bool = False) -> None:
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if group:
            raise APIError(409, "同名标签分组已存在", code="tag_group_name_conflict") from error
        raise APIError(
            409, "同名标签已存在，请选择其他名称或合并标签", code="tag_name_conflict"
        ) from error


def get_group(db: Session, group_id: int) -> TagGroup:
    group = db.get(TagGroup, group_id)
    if group is None:
        raise APIError(404, "标签分组不存在", code="tag_group_not_found")
    return group


def get_tag(db: Session, tag_id: int) -> Tag:
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise APIError(404, "标签不存在", code="tag_not_found")
    return tag


def tag_dict(db: Session, tag: Tag, counts: dict[bool, int] | None = None) -> dict[str, Any]:
    if counts is None:
        counts = {
            bool(trash): count
            for trash, count in db.execute(
                select(Video.deleted_at.is_not(None), func.count())
                .join(video_tags, video_tags.c.video_id == Video.id)
                .where(video_tags.c.tag_id == tag.id)
                .group_by(Video.deleted_at.is_not(None))
            ).all()
        }
    return {
        "id": tag.id,
        "name": tag.name,
        "color": tag.color,
        "group_id": tag.group_id,
        "group_name": tag.group.name if tag.group else None,
        "count": counts.get(False, 0),
        "trash_count": counts.get(True, 0),
    }


def rewrite_uploads(db: Session, old: str, new: str | None) -> None:
    # Incomplete resumable uploads must not recreate a removed/renamed tag.
    with db.no_autoflush:
        for upload in db.scalars(select(Upload)):
            if old in upload.tags:
                upload.tags = list(
                    dict.fromkeys(
                        new if name == old and new is not None else name
                        for name in upload.tags
                        if name != old or new is not None
                    )
                )


@router.get("")
def list_tags(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    counts: dict[int, dict[bool, int]] = {}
    for tag_id, trash, count in db.execute(
        select(video_tags.c.tag_id, Video.deleted_at.is_not(None), func.count())
        .join(Video, Video.id == video_tags.c.video_id)
        .group_by(video_tags.c.tag_id, Video.deleted_at.is_not(None))
    ):
        counts.setdefault(tag_id, {})[bool(trash)] = count
    return [
        tag_dict(db, tag, counts.get(tag.id, {}))
        for tag in db.scalars(select(Tag).order_by(Tag.name))
    ]


@router.get("/groups")
def list_groups(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    counts = dict(db.execute(select(Tag.group_id, func.count()).group_by(Tag.group_id)).all())
    return [
        {"id": group.id, "name": group.name, "count": counts.get(group.id, 0)}
        for group in db.scalars(select(TagGroup).order_by(TagGroup.name))
    ]


@router.post("/groups")
def create_group(body: NamedBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    lock(db)
    group = TagGroup(name=body.name)
    db.add(group)
    commit(db, group=True)
    return {"id": group.id, "name": group.name, "count": 0}


@router.patch("/groups/{group_id}")
def rename_group(group_id: int, body: NamedBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    lock(db)
    group = get_group(db, group_id)
    group.name = body.name
    commit(db, group=True)
    return {"id": group.id, "name": group.name}


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    lock(db)
    db.delete(get_group(db, group_id))
    commit(db, group=True)
    return {"ok": True}


@router.post("")
def create_tag(body: TagBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    lock(db)
    group = get_group(db, body.group_id) if body.group_id else None
    tag = Tag(name=body.name, color=body.color, group=group)
    db.add(tag)
    commit(db)
    return tag_dict(db, tag)


class TagPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    color: str | None = None
    group_id: int | None = Field(None, gt=0)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Name cannot be null")
        return NamedBody.clean_name(value)

    @field_validator("color")
    @classmethod
    def clean_color(cls, value: str | None) -> str | None:
        return TagBody.clean_color(value)


@router.patch("/{tag_id}")
def update_tag(tag_id: int, body: TagPatch, db: Session = Depends(get_db)) -> dict[str, Any]:
    lock(db)
    tag = get_tag(db, tag_id)
    group = get_group(db, body.group_id) if body.group_id else None
    old = tag.name
    if body.name is not None:
        tag.name = body.name
        rewrite_uploads(db, old, body.name)
    if "color" in body.model_fields_set:
        tag.color = body.color
    if "group_id" in body.model_fields_set:
        tag.group = group
    commit(db)
    return tag_dict(db, tag)


class MergeBody(BaseModel):
    target_id: int = Field(gt=0)


@router.post("/{tag_id}/merge")
def merge_tag(tag_id: int, body: MergeBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    lock(db)
    if tag_id == body.target_id:
        raise APIError(400, "不能将标签合并到自身", code="tag_self_merge")
    source, target = get_tag(db, tag_id), get_tag(db, body.target_id)
    updated = (
        db.scalar(
            select(func.count()).select_from(video_tags).where(video_tags.c.tag_id == source.id)
        )
        or 0
    )
    db.execute(
        insert(video_tags)
        .from_select(
            ["video_id", "tag_id"],
            select(video_tags.c.video_id, literal(target.id)).where(
                video_tags.c.tag_id == source.id
            ),
        )
        .on_conflict_do_nothing()
    )
    rewrite_uploads(db, source.name, target.name)
    db.execute(delete(video_tags).where(video_tags.c.tag_id == source.id))
    db.delete(source)
    commit(db)
    return {"tag": tag_dict(db, target), "updated": updated}


@router.delete("/{tag_id}")
def delete_tag(tag_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    lock(db)
    tag = get_tag(db, tag_id)
    rewrite_uploads(db, tag.name, None)
    db.execute(delete(video_tags).where(video_tags.c.tag_id == tag.id))
    db.delete(tag)
    commit(db)
    return {"ok": True}
