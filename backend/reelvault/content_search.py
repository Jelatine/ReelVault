"""Plain-text, timed subtitle indexing. All values passed to FTS are bound parameters."""

from __future__ import annotations

import html
import json
import logging
import re
from typing import Any

from sqlalchemy import column, delete, func, insert, literal_column, select, table, text
from sqlalchemy.orm import Session

from .config import Settings
from .library import abs_path
from .media.scenes import source_signature
from .models import MediaAsset, SubtitleCue, SubtitleTrack, Video
from .search_syntax import parse_search

MAX_BYTES = 5 * 1024 * 1024
MAX_CUES = 50000
TIMING = re.compile(r"(?:(\d{2,}):)?(\d{2}):(\d{2})\.(\d{3})$")


async def backfill(db: Session, settings: Settings) -> None:
    from .media.ffmpeg import FFmpegError
    from .media.subtitles import to_vtt

    for track in db.scalars(select(SubtitleTrack).where(SubtitleTrack.source_signature.is_(None))):
        video, asset = db.get(Video, track.video_id), db.get(MediaAsset, track.asset_id)
        if not video or not asset:
            continue
        try:
            cache = settings.assets_dir / f"{asset.id}.webvtt"
            data = (
                cache.read_bytes()
                if cache.is_file()
                else await to_vtt(settings.ffmpeg, abs_path(settings, asset.file_path))
            )
            signature = source_signature(abs_path(settings, video.file_path))
            # A savepoint keeps one invalid legacy subtitle from discarding other indexes.
            with db.begin_nested():
                index_track(db, track, data)
                track.source_signature = signature
        except (OSError, FFmpegError, TimeoutError, ValueError, UnicodeError):
            logging.getLogger("reelvault").warning("could not index an unavailable legacy subtitle")
    db.commit()


def timestamp(value: str) -> float:
    match = TIMING.fullmatch(value)
    if not match:
        raise ValueError("字幕时间格式无效")
    hours, minutes, seconds, millis = match.groups()
    if int(minutes) >= 60 or int(seconds) >= 60:
        raise ValueError("字幕时间格式无效")
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def parse_vtt(data: bytes) -> list[dict[str, Any]]:
    if len(data) > MAX_BYTES:
        raise ValueError("字幕不能超过 5 MiB")
    content = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    if not content.startswith("WEBVTT"):
        raise ValueError("字幕格式无效")
    cues = []
    for block in re.split(r"\n\s*\n", content):
        lines = block.splitlines()
        if not lines or lines[0].startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        timing_index = next((i for i, line in enumerate(lines[:2]) if " --> " in line), None)
        if timing_index is None:
            continue
        start, end = lines[timing_index].split(" --> ", 1)
        a, b = timestamp(start.strip()), timestamp(end.split()[0])
        cue_text = html.unescape(re.sub(r"<[^>]*>", "", "\n".join(lines[timing_index + 1 :])))
        cue_text = " ".join(cue_text.split())
        if b <= a or not cue_text:
            continue
        cues.append({"start": a, "end": b, "text": cue_text[:8192]})
        if len(cues) > MAX_CUES:
            raise ValueError("字幕片段超过 50000 个")
    return cues


def index_track(db: Session, track: SubtitleTrack, data: bytes) -> int:
    cues = parse_vtt(data)
    db.flush()
    db.execute(delete(SubtitleCue).where(SubtitleCue.track_id == track.id))
    if cues:
        db.execute(insert(SubtitleCue), [{"track_id": track.id, **cue} for cue in cues])
    db.flush()
    return len(cues)


def track_current(settings: Settings, video: Video, track: SubtitleTrack) -> bool:
    try:
        current = source_signature(abs_path(settings, video.file_path))
        return not track.source_signature or track.source_signature == current
    except OSError:
        return False


def search_content(
    db: Session,
    settings: Settings,
    q: str,
    *,
    video_id: str | None = None,
    page: int = 1,
    page_size: int = 30,
) -> dict[str, Any]:
    parsed = parse_search(q)
    if not parsed.terms:
        return {"items": [], "total": 0, "page": page, "page_size": page_size}
    stmt = (
        select(SubtitleCue, SubtitleTrack, Video)
        .join(SubtitleTrack, SubtitleTrack.id == SubtitleCue.track_id)
        .join(Video, Video.id == SubtitleTrack.video_id)
        .where(
            Video.deleted_at.is_(None),
            Video.status == "ready",
            SubtitleCue.start < Video.duration,
            *parsed.conditions,
        )
    )
    if video_id is not None:
        stmt = stmt.where(Video.id == video_id)
    long_terms = [term for term in parsed.terms if len(term) >= 3]
    if long_terms:
        index = table("subtitle_search", column("rowid"))
        stmt = (
            stmt.join(index, index.c.rowid == SubtitleCue.id)
            .where(text("subtitle_search MATCH :cue_query"))
            .params(
                cue_query=" AND ".join('"' + term.replace('"', '""') + '"' for term in long_terms)
            )
        )
    for term in parsed.terms:
        if term not in long_terms:
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            stmt = stmt.where(SubtitleCue.text.like(f"%{escaped}%", escape="\\"))
    # Stat only matching tracks. A replaced/unavailable source must never offer old timestamps.
    valid = [
        track.id
        for track, video in db.execute(
            select(SubtitleTrack, Video)
            .join(Video, Video.id == SubtitleTrack.video_id)
            .where(SubtitleTrack.id.in_(select(stmt.subquery().c.track_id)))
            .distinct()
        )
        if track_current(settings, video, track)
    ]
    if not valid:
        return {"items": [], "total": 0, "page": page, "page_size": page_size}
    # One bound JSON value also works for libraries exceeding SQLite's variable limit.
    allowed = func.json_each(json.dumps(valid)).table_valued("value")
    stmt = stmt.where(SubtitleTrack.id.in_(select(allowed.c.value)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    if long_terms:
        stmt = stmt.order_by(literal_column("bm25(subtitle_search)"))
    stmt = stmt.order_by(Video.id, SubtitleCue.start, SubtitleCue.id)
    items = [
        {
            "id": cue.id,
            "video_id": video.id,
            "title": video.title,
            "track_id": track.id,
            "label": track.label,
            "language": track.language,
            "start": cue.start,
            "end": cue.end,
            "text": cue.text,
            "url": f"/videos/{video.id}?t={cue.start:.3f}",
        }
        for cue, track, video in db.execute(stmt.offset((page - 1) * page_size).limit(page_size))
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}
