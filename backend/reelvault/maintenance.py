"""Periodic retention maintenance, protecting media used by active jobs."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .library import delete_video_files
from .models import Job, Video, utcnow

log = logging.getLogger("reelvault.maintenance")


def purge_expired_trash(settings: Settings, sessions: sessionmaker[Session]) -> int:
    if settings.trash_retention_days == 0:
        return 0
    cutoff = utcnow() - timedelta(days=settings.trash_retention_days)
    deleted = 0
    with sessions() as db:
        active_ids = {
            video_id
            for job in db.scalars(
                select(Job).where(Job.status.in_(["queued", "running", "paused"]))
            )
            for video_id in job.video_ids
        }
        for video in db.scalars(select(Video).where(Video.deleted_at < cutoff)):
            if video.id in active_ids:
                continue
            try:
                delete_video_files(settings, video)
            except OSError:
                log.exception("cannot purge expired video %s", video.id)
                continue
            db.delete(video)
            deleted += 1
        db.commit()
    return deleted


async def maintain(settings: Settings, sessions: sessionmaker[Session]) -> None:
    while True:
        try:
            count = await asyncio.to_thread(purge_expired_trash, settings, sessions)
            if count:
                log.info("purged %s expired videos", count)
        except Exception:
            log.exception("trash retention maintenance failed")
        await asyncio.sleep(3600)
