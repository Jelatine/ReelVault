from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import get_db
from ..grouping import group_condition, parse_group
from ..metadata import metadata_values
from ..models import Video

router = APIRouter(
    prefix="/api/auto-groups", tags=["auto-groups"], dependencies=[Depends(require_auth)]
)
ACTIVE = (Video.deleted_at.is_(None), Video.status == "ready")


def group_dict(key: str, count: int, video: Video | None = None) -> dict[str, Any]:
    kind, value = parse_group(key)
    result: dict[str, Any] = {"key": key, "kind": kind, "value": value, "count": count}
    if kind == "device":
        metadata = metadata_values(video) if video else {}
        result.update({key: metadata.get(key) for key in ("device_make", "device_model")})
    return result


@router.get("")
def groups(db: Session = Depends(get_db)) -> dict[str, Any]:
    month = func.strftime("%Y-%m", Video.captured_at)
    years: dict[str, dict[str, Any]] = {}
    for value, count in db.execute(
        select(month, func.count())
        .where(*ACTIVE, Video.captured_at.is_not(None))
        .group_by(month)
        .order_by(month.desc())
    ):
        year = value[:4]
        item = years.setdefault(year, {"group": group_dict(f"year:{year}", 0), "months": []})
        item["group"]["count"] += count
        item["months"].append(group_dict(f"month:{value}", count))
    device = func.reelvault_device(Video.meta, Video.metadata_overrides)
    counts = (
        select(
            device.label("device"),
            func.count().label("count"),
            func.min(Video.id).label("video_id"),
        )
        .where(*ACTIVE)
        .group_by(device)
        .subquery()
    )
    devices = [
        group_dict(f"device:{key}", count, video)
        for key, count, video in db.execute(
            select(counts.c.device, counts.c.count, Video).join(
                Video, Video.id == counts.c.video_id
            )
        )
    ]
    devices.sort(
        key=lambda g: (
            g["value"] == "unknown",
            g.get("device_make") or "",
            g.get("device_model") or "",
        )
    )
    resolutions = []
    for value in ("portrait", "landscape", "square", "4k", "unknown"):
        key = f"resolution:{value}"
        count = (
            db.scalar(select(func.count()).select_from(Video).where(*ACTIVE, group_condition(key)))
            or 0
        )
        if count:
            resolutions.append(group_dict(key, count))
    unknown_date = (
        db.scalar(
            select(func.count()).select_from(Video).where(*ACTIVE, Video.captured_at.is_(None))
        )
        or 0
    )
    return {
        "years": list(years.values()),
        "devices": devices,
        "resolutions": resolutions,
        "unknown_date": group_dict("date:unknown", unknown_date) if unknown_date else None,
        "total": db.scalar(select(func.count()).select_from(Video).where(*ACTIVE)) or 0,
    }


@router.get("/{key}")
def group_detail(key: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    condition = group_condition(key)
    count = db.scalar(select(func.count()).select_from(Video).where(*ACTIVE, condition)) or 0
    video = db.scalar(select(Video).where(*ACTIVE, condition).order_by(Video.id).limit(1))
    return group_dict(key, count, video)
