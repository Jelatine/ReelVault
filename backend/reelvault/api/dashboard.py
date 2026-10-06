from __future__ import annotations

import shutil
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import CurrentAuth, require_auth
from ..config import Settings
from ..db import get_db
from ..library import video_to_dict
from ..models import HlsPackage, Playback, Video
from .deps import get_settings

router = APIRouter(tags=["dashboard"])


@router.get("/api/dashboard")
def dashboard(
    response: Response,
    limit: int = Query(8, ge=1, le=24),
    auth: CurrentAuth = Depends(require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    base = select(Video).where(Video.deleted_at.is_(None))
    watching = db.execute(
        select(Video, Playback)
        .join(Playback, Playback.video_id == Video.id)
        .where(
            Video.deleted_at.is_(None),
            Video.status == "ready",
            Playback.user_id == auth.user_id,
            Playback.last_played_at.is_not(None),
            Playback.position > 1,
            Playback.position < Video.duration - 1,
        )
        .order_by(Playback.last_played_at.desc(), Video.id)
        .limit(limit)
    )
    sections = {
        "continue_watching": [
            {
                **video_to_dict(video),
                "position": row.position,
                "last_played_at": row.last_played_at.isoformat() if row.last_played_at else None,
            }
            for video, row in watching
        ],
        "recent_added": [
            video_to_dict(v)
            for v in db.scalars(base.order_by(Video.created_at.desc(), Video.id).limit(limit))
        ],
        "recent_edited": [
            video_to_dict(v)
            for v in db.scalars(
                base.where(Video.edited_at.is_not(None))
                .order_by(Video.edited_at.desc(), Video.id)
                .limit(limit)
            )
        ],
        "favorites": [
            video_to_dict(v)
            for v in db.scalars(
                base.where(Video.favorite.is_(True))
                .order_by(Video.created_at.desc(), Video.id)
                .limit(limit)
            )
        ],
    }
    library = db.execute(
        select(func.count(), func.coalesce(func.sum(Video.size), 0)).where(
            Video.deleted_at.is_(None)
        )
    ).one()
    trash = db.execute(
        select(func.count(), func.coalesce(func.sum(Video.size), 0)).where(
            Video.deleted_at.is_not(None)
        )
    ).one()
    usage = shutil.disk_usage(settings.data_dir)
    return {
        **sections,
        "storage": {
            "library": {"count": library[0], "size": library[1]},
            "trash": {"count": trash[0], "size": trash[1]},
            "hls_size": int(db.scalar(select(func.coalesce(func.sum(HlsPackage.size), 0))) or 0),
            "disk": {"total": usage.total, "used": usage.used, "free": usage.free},
        },
    }
