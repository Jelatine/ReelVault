from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import column, delete, func, or_, select, table, text
from sqlalchemy.orm import Session

from ..auth import CurrentAuth, require_auth
from ..db import get_db
from ..errors import APIError
from ..models import Folder, RecentSearch, Tag, Video, utcnow
from ..pinyin_search import normalize_pinyin_query
from ..search_syntax import parse_search

router = APIRouter(prefix="/api/search", tags=["search"])
SearchID = Annotated[int, Path(gt=0, le=2**63 - 1)]


def history_dict(record: RecentSearch) -> dict[str, Any]:
    return {"id": record.id, "query": record.query, "used_at": record.used_at}


@router.get("/recent")
def recent(
    auth: CurrentAuth = Depends(require_auth), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return [
        history_dict(row)
        for row in db.scalars(
            select(RecentSearch)
            .where(RecentSearch.user_id == auth.user_id)
            .order_by(RecentSearch.used_at.desc(), RecentSearch.id.desc())
            .limit(20)
        )
    ]


class SearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=512)

    @field_validator("query")
    @classmethod
    def valid_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise APIError(400, "搜索内容不能为空", code="search_query_empty")
        parse_search(value)
        return value


@router.post("/recent")
def remember(
    body: SearchBody, auth: CurrentAuth = Depends(require_auth), db: Session = Depends(get_db)
) -> dict[str, Any]:
    db.execute(text("BEGIN IMMEDIATE"))
    row = db.scalar(
        select(RecentSearch).where(
            RecentSearch.user_id == auth.user_id, RecentSearch.query == body.query
        )
    )
    if row is None:
        row = RecentSearch(user_id=auth.user_id, query=body.query)
        db.add(row)
    row.used_at = utcnow()
    db.flush()
    stale = (
        select(RecentSearch.id)
        .where(RecentSearch.user_id == auth.user_id)
        .order_by(RecentSearch.used_at.desc(), RecentSearch.id.desc())
        .offset(20)
    )
    db.execute(delete(RecentSearch).where(RecentSearch.id.in_(stale)))
    db.commit()
    return history_dict(row)


@router.delete("/recent")
def clear(
    auth: CurrentAuth = Depends(require_auth), db: Session = Depends(get_db)
) -> dict[str, bool]:
    db.execute(text("BEGIN IMMEDIATE"))
    db.execute(delete(RecentSearch).where(RecentSearch.user_id == auth.user_id))
    db.commit()
    return {"ok": True}


@router.delete("/recent/{search_id}")
def forget(
    search_id: SearchID, auth: CurrentAuth = Depends(require_auth), db: Session = Depends(get_db)
) -> dict[str, bool]:
    db.execute(text("BEGIN IMMEDIATE"))
    row = db.scalar(
        select(RecentSearch).where(
            RecentSearch.id == search_id, RecentSearch.user_id == auth.user_id
        )
    )
    if row is None:
        raise APIError(404, "最近搜索不存在", code="recent_search_not_found")
    db.delete(row)
    db.commit()
    return {"ok": True}


def escaped_like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@router.get("/suggestions")
def suggestions(
    q: str = Query("", max_length=512),
    auth: CurrentAuth = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    term = q.strip()
    if not term:
        return {"videos": [], "tags": [], "folders": []}
    phonetic = normalize_pinyin_query(term)
    index = table(
        "video_search",
        *(column(c) for c in ("video_id", "title", "title_pinyin", "title_initials")),
    )
    videos = (
        select(Video)
        .join(index, index.c.video_id == Video.id)
        .where(Video.deleted_at.is_(None), Video.status == "ready")
    )
    literal = func.reelvault_casefold(Video.title).like(escaped_like(term.casefold()), escape="\\")
    if len(term) >= 3 and (phonetic is None or len(phonetic) >= 3):
        expression = 'title:"' + term.replace('"', '""') + '"'
        if phonetic:
            expression = "(" + expression + ' OR {title_pinyin title_initials}:"' + phonetic + '")'
        videos = videos.where(text("video_search MATCH :suggest_query")).params(
            suggest_query=expression
        )
    else:
        conditions = [literal]
        if phonetic:
            conditions.extend(
                index.c[c].like(f"%{phonetic}%") for c in ("title_pinyin", "title_initials")
            )
        videos = videos.where(or_(*conditions))
    video_rows = db.scalars(videos.order_by(literal.desc(), Video.title, Video.id).limit(6)).all()

    def names(model):
        return db.scalars(
            select(model)
            .where(
                func.reelvault_casefold(model.name).like(escaped_like(term.casefold()), escape="\\")
            )
            .order_by(model.name, model.id)
            .limit(6)
        ).all()

    return {
        "videos": [{"id": v.id, "name": v.title} for v in video_rows],
        "tags": [{"id": t.id, "name": t.name} for t in names(Tag)],
        "folders": [{"id": f.id, "name": f.name, "parent_id": f.parent_id} for f in names(Folder)],
    }
