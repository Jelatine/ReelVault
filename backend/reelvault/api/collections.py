from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import get_db
from ..library import video_to_dict
from ..models import Collection, CollectionItem, Video

router = APIRouter(
    prefix="/api/collections", tags=["collections"], dependencies=[Depends(require_auth)]
)


def get_collection(db: Session, collection_id: int) -> Collection:
    collection = db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(404, "合集不存在")
    return collection


def collection_dict(collection: Collection, count: int) -> dict[str, Any]:
    return {
        "id": collection.id,
        "name": collection.name,
        "description": collection.description,
        "count": count,
    }


def detail(db: Session, collection: Collection) -> dict[str, Any]:
    videos = db.scalars(
        select(Video)
        .join(CollectionItem, CollectionItem.video_id == Video.id)
        .where(CollectionItem.collection_id == collection.id, Video.deleted_at.is_(None))
        .order_by(CollectionItem.position, CollectionItem.video_id)
    ).all()
    return {
        **collection_dict(collection, len(videos)),
        "items": [video_to_dict(video) for video in videos],
    }


@router.get("")
def list_collections(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    counts = dict(
        db.execute(
            select(CollectionItem.collection_id, func.count())
            .join(Video, Video.id == CollectionItem.video_id)
            .where(Video.deleted_at.is_(None))
            .group_by(CollectionItem.collection_id)
        ).all()
    )
    return [
        collection_dict(collection, counts.get(collection.id, 0))
        for collection in db.scalars(select(Collection).order_by(Collection.name))
    ]


class CollectionBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field("", max_length=10000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("合集名称不能为空")
        return value.strip()


def commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, "同名合集已存在或合集内容已变化，请刷新后重试") from error


class CollectionCreate(CollectionBody):
    video_ids: list[str] = Field(default_factory=list, max_length=1000)


@router.post("")
def create_collection(body: CollectionCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    ids = list(dict.fromkeys(body.video_ids))
    if ids:
        existing = db.scalars(
            select(Video.id).where(Video.id.in_(ids), Video.deleted_at.is_(None))
        ).all()
        if len(existing) != len(ids):
            raise HTTPException(404, "部分视频不存在或已删除")
    collection = Collection(name=body.name, description=body.description)
    db.add(collection)
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, "同名合集已存在") from error
    db.add_all(
        [
            CollectionItem(collection_id=collection.id, video_id=video_id, position=index)
            for index, video_id in enumerate(ids)
        ]
    )
    commit(db)
    return detail(db, collection)


@router.get("/{collection_id}")
def collection_detail(collection_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    return detail(db, get_collection(db, collection_id))


@router.put("/{collection_id}")
def update_collection(
    collection_id: int, body: CollectionBody, db: Session = Depends(get_db)
) -> dict[str, Any]:
    collection = get_collection(db, collection_id)
    collection.name, collection.description = body.name, body.description
    commit(db)
    return detail(db, collection)


@router.delete("/{collection_id}")
def delete_collection(collection_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    db.delete(get_collection(db, collection_id))
    db.commit()
    return {"ok": True}


class MembersBody(BaseModel):
    video_ids: list[str] = Field(min_length=1, max_length=1000)


@router.post("/{collection_id}/items")
def add_items(
    collection_id: int, body: MembersBody, db: Session = Depends(get_db)
) -> dict[str, Any]:
    collection = get_collection(db, collection_id)
    ids = list(dict.fromkeys(body.video_ids))
    videos = db.scalars(select(Video).where(Video.id.in_(ids), Video.deleted_at.is_(None))).all()
    if len(videos) != len(ids):
        raise HTTPException(404, "部分视频不存在或已删除")
    maximum = db.scalar(
        select(func.max(CollectionItem.position)).where(
            CollectionItem.collection_id == collection_id
        )
    )
    position = (maximum + 1) if maximum is not None else 0
    for video_id in ids:
        statement = insert(CollectionItem).values(
            collection_id=collection_id, video_id=video_id, position=position
        )
        db.execute(statement.on_conflict_do_nothing(index_elements=["collection_id", "video_id"]))
        position += 1
    commit(db)
    return detail(db, collection)


@router.delete("/{collection_id}/items/{video_id}")
def remove_item(collection_id: int, video_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    collection = get_collection(db, collection_id)
    item = db.get(CollectionItem, (collection_id, video_id))
    if item is not None:
        db.delete(item)
        db.commit()
    return detail(db, collection)


class OrderBody(BaseModel):
    video_ids: list[str]


@router.put("/{collection_id}/order")
def reorder(collection_id: int, body: OrderBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    collection = get_collection(db, collection_id)
    items = db.scalars(
        select(CollectionItem)
        .where(CollectionItem.collection_id == collection_id)
        .order_by(CollectionItem.position)
    ).all()
    visible = {video["id"] for video in detail(db, collection)["items"]}
    if len(set(body.video_ids)) != len(body.video_ids) or set(body.video_ids) != visible:
        raise HTTPException(409, "排序必须包含全部当前可见成员，请刷新后重试")
    # Keep soft-deleted members so restoring a video also restores membership.
    hidden = [item.video_id for item in items if item.video_id not in visible]
    positions = {video_id: index for index, video_id in enumerate([*body.video_ids, *hidden])}
    for item in items:
        item.position = positions[item.video_id]
    commit(db)
    return detail(db, collection)
