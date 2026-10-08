"""A handler must release its write lock before dependency/response cleanup."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from reelvault.ai import AiParams
from reelvault.api import ai, playback_cache, transcription, visual_search
from reelvault.api import jobs as jobs_api
from reelvault.errors import APIError
from reelvault.models import Job
from reelvault.transcription import TranscriptionParams
from reelvault.vision import VisionParams

from .conftest import upload_ready


@pytest.mark.parametrize(
    "kind",
    [
        "cached",
        "playable",
        "transcribe",
        "vision_index",
        "ai_analyze",
        "rejected",
        "clear_busy",
        "manager_rejected",
        "retry_rejected",
    ],
)
def test_task_handlers_release_sqlite_writer_before_response_cleanup(
    client, settings, samples, monkeypatch, kind
):
    video = upload_ready(client, samples["a"])
    settings.transcription_enabled = settings.vision_enabled = settings.ai_enabled = True
    monkeypatch.setattr("reelvault.transcription.available", lambda settings: True)
    jobs = client.app.state.jobs
    with client.app.state.sessionmaker() as db:
        if kind not in {"cached", "rejected", "manager_rejected"}:
            row = Job(
                kind="vision_index" if kind == "clear_busy" else kind,
                status="paused",
                video_ids=[video["id"]],
                params={},
            )
            db.add(row)
            db.commit()
        if kind == "manager_rejected":
            with pytest.raises(APIError):
                jobs.submit(
                    db, "edit", {"edit": {"op": "compress", "codec": "h264", "crf": 30}}, ["0" * 32]
                )
        elif kind == "retry_rejected":
            with pytest.raises(APIError):
                asyncio.run(jobs_api.retry_job(row.id, db=db, jobs=jobs))
        elif kind in {"cached", "playable", "rejected"}:
            if kind == "rejected":
                with pytest.raises(APIError):
                    playback_cache.submit("0" * 32, db=db, settings=settings, jobs=jobs)
            else:
                playback_cache.submit(video["id"], db=db, settings=settings, jobs=jobs)
        elif kind == "transcribe":
            transcription.submit(
                video["id"], TranscriptionParams(), db=db, settings=settings, jobs=jobs
            )
        elif kind == "vision_index":
            visual_search.submit(video["id"], VisionParams(), db=db, settings=settings, jobs=jobs)
        elif kind == "ai_analyze":
            ai.submit(video["id"], AiParams(), db=db, settings=settings, jobs=jobs)
        else:
            with pytest.raises(APIError):
                asyncio.run(visual_search.clear(video["id"], db=db, settings=settings, jobs=jobs))
        # The handler has returned, but its dependency has deliberately NOT closed
        # db yet. Progress/control writes from another worker must already succeed.
        with sqlite3.connect(settings.db_path, timeout=0.1) as other:
            other.execute("BEGIN IMMEDIATE")
            other.execute("UPDATE jobs SET message=message WHERE status='paused'")
