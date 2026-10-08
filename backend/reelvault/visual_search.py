from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .config import Settings
from .library import abs_path
from .media.scenes import source_signature
from .models import Video, VideoVectorIndex
from .vision import MODEL_ID, packed


def current(settings: Settings, video: Video, index: VideoVectorIndex) -> bool:
    try:
        return index.model == MODEL_ID and index.signature == source_signature(
            abs_path(settings, video.file_path)
        )
    except (OSError, ValueError):
        return False


def directory(settings: Settings, index: VideoVectorIndex) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", index.video_id) or not re.fullmatch(
        r"[a-f0-9]{32}", index.generation
    ):
        raise ValueError("Invalid frame generation")
    return settings.derived_dir / index.video_id / "vision" / index.generation


def rank(
    db: Session,
    settings: Settings,
    values: list[float],
    video_id: str | None = None,
    page: int = 1,
    page_size: int = 30,
) -> dict[str, Any]:
    statement = (
        select(Video, VideoVectorIndex)
        .join(VideoVectorIndex)
        .where(
            Video.status == "ready", Video.deleted_at.is_(None), VideoVectorIndex.model == MODEL_ID
        )
    )
    if video_id:
        statement = statement.where(Video.id == video_id)
    ids = [v.id for v, index in db.execute(statement) if current(settings, v, index)]
    params = {
        "ids": json.dumps(ids),
        "vector": packed(values),
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    # Ordinary tables keep backup/restore portable. Native C distance avoids Python
    # loops over every frame, and a JSON binding avoids SQLite's parameter limit.
    ranked = """WITH ranked AS (
      SELECT f.id, f.video_id, f.timestamp, v.title, i.generation,
        1 - vec_distance_cosine(f.embedding, :vector) AS score,
        row_number() OVER (PARTITION BY f.video_id ORDER BY
          vec_distance_cosine(f.embedding, :vector), f.timestamp, f.id) AS position
      FROM vector_frames f JOIN videos v ON v.id = f.video_id
      JOIN video_vector_indexes i ON i.video_id = f.video_id
      WHERE f.video_id IN (SELECT value FROM json_each(:ids))
        AND f.timestamp < v.duration
    ) """
    where = "" if video_id else " WHERE position = 1"
    total = db.scalar(text(ranked + "SELECT count(*) FROM ranked" + where), params) or 0
    items = []
    for row in db.execute(
        text(
            ranked
            + "SELECT * FROM ranked"
            + where
            + " ORDER BY score DESC, video_id, timestamp LIMIT :limit OFFSET :offset"
        ),
        params,
    ).mappings():
        items.append(
            {
                "id": row["id"],
                "video_id": row["video_id"],
                "title": row["title"],
                "time": row["timestamp"],
                "score": round(max(-1, min(1, row["score"])), 6),
                "thumbnail": (
                    f"/api/visual-search/frames/{row['id']}?generation={row['generation']}"
                ),
                "url": f"/videos/{row['video_id']}?t={row['timestamp']:.6f}",
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size, "model": MODEL_ID}
