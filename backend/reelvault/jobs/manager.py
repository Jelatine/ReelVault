"""In-process job queue: runs ffmpeg work on a pool of asyncio workers and broadcasts
progress to SSE subscribers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..media.ffmpeg import Canceled, ProcessHandle
from ..models import Job, utcnow

log = logging.getLogger("reelvault.jobs")


class JobContext:
    def __init__(self, manager: JobManager, job_id: str) -> None:
        self.manager = manager
        self.job_id = job_id
        self.handle = ProcessHandle()
        self._last_flush = 0.0
        self.progress = 0.0
        self.message = ""

    @property
    def settings(self) -> Settings:
        return self.manager.settings

    def db(self) -> Session:
        return self.manager.sessionmaker()

    def set_progress(self, value: float, message: str | None = None) -> None:
        self.progress = max(0.0, min(1.0, value))
        if message is not None:
            self.message = message
        now = time.monotonic()
        if now - self._last_flush < 0.5 and value < 1.0 and message is None:
            return
        self._last_flush = now
        with self.db() as db:
            job = db.get(Job, self.job_id)
            if job is not None:
                job.progress = self.progress
                job.message = self.message
                db.commit()
                self.manager.publish(job)

    def stage(self, index: int, count: int, message: str) -> Callable[[float], None]:
        """Progress callback mapping a sub-step's 0..1 to its slice of the whole job."""
        self.set_progress(index / count, message)

        def cb(frac: float) -> None:
            self.set_progress((index + frac) / count)

        return cb

    def check_canceled(self) -> None:
        if self.handle.canceled:
            raise Canceled()


Handler = Callable[[JobContext, Job], Awaitable[None]]


def job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "params": job.params,
        "video_ids": job.video_ids,
        "result_video_id": job.result_video_id,
        "has_result_file": bool(job.result_file),
        "progress": round(job.progress, 4),
        "message": job.message,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


class JobManager:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: sessionmaker[Session],
        handlers: dict[str, Handler],
    ) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.handlers = handlers
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.running: dict[str, JobContext] = {}
        self.subscribers: set[asyncio.Queue[str]] = set()
        self._tasks: list[asyncio.Task[None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        with self.sessionmaker() as db:
            for job in db.scalars(select(Job).where(Job.status == "running")):
                job.status = "failed"
                job.error = "服务重启，任务被中断"
                job.finished_at = utcnow()
            db.commit()
            queued = db.scalars(
                select(Job.id).where(Job.status == "queued").order_by(Job.created_at)
            ).all()
        for job_id in queued:
            self.queue.put_nowait(job_id)
        for i in range(max(1, self.settings.workers)):
            self._tasks.append(asyncio.create_task(self._worker(i), name=f"job-worker-{i}"))

    async def stop(self) -> None:
        for ctx in list(self.running.values()):
            ctx.handle.cancel()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks.clear()

    # ------------------------------------------------------------ API

    def submit(self, db: Session, kind: str, params: dict[str, Any], video_ids: list[str]) -> Job:
        job = Job(kind=kind, params=params, video_ids=video_ids, message="排队中")
        db.add(job)
        db.commit()
        self.enqueue(job.id)
        self.publish(job)
        return job

    def submit_from_worker(self, kind: str, params: dict[str, Any], video_ids: list[str]) -> str:
        with self.sessionmaker() as db:
            return self.submit(db, kind, params, video_ids).id

    def enqueue(self, job_id: str) -> None:
        if self._loop is not None and self._loop.is_running():
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is self._loop:
                self.queue.put_nowait(job_id)
            else:
                self._loop.call_soon_threadsafe(self.queue.put_nowait, job_id)
        else:
            self.queue.put_nowait(job_id)

    def cancel(self, db: Session, job: Job) -> None:
        if job.status == "queued":
            job.status = "canceled"
            job.finished_at = utcnow()
            job.message = "已取消"
            db.commit()
            self.publish(job)
        elif job.status == "running" and job.id in self.running:
            self.running[job.id].handle.cancel()

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self.subscribers.discard(q)

    def publish(self, job: Job) -> None:
        payload = json.dumps(job_to_dict(job), ensure_ascii=False)

        def deliver() -> None:
            for q in list(self.subscribers):
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(payload)

        if self._loop is None:
            return
        try:
            same = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            same = False
        if same:
            deliver()
        elif self._loop.is_running():
            self._loop.call_soon_threadsafe(deliver)

    # ------------------------------------------------------------ worker

    async def _worker(self, n: int) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                await self._run(job_id)
            except Exception:  # pragma: no cover - defensive
                log.exception("job %s crashed", job_id)
            finally:
                self.queue.task_done()

    async def _run(self, job_id: str) -> None:
        with self.sessionmaker() as db:
            job = db.get(Job, job_id)
            if job is None or job.status != "queued":
                return
            job.status = "running"
            job.started_at = utcnow()
            job.message = "处理中"
            db.commit()
            self.publish(job)
            db.expunge(job)

        ctx = JobContext(self, job_id)
        self.running[job_id] = ctx
        handler = self.handlers.get(job.kind)
        status, error, message = "succeeded", None, "完成"
        try:
            if handler is None:
                raise RuntimeError(f"未知任务类型 {job.kind}")
            await handler(ctx, job)
        except Canceled:
            status, message = "canceled", "已取消"
        except Exception as e:
            log.warning("job %s (%s) failed: %s", job_id, job.kind, e)
            status, error, message = "failed", str(e) or type(e).__name__, "失败"
        finally:
            self.running.pop(job_id, None)

        with self.sessionmaker() as db:
            final = db.get(Job, job_id)
            if final is None:
                return
            final.status = status
            final.error = error
            final.message = message
            if status == "succeeded":
                final.progress = 1.0
            final.finished_at = utcnow()
            db.commit()
            self.publish(final)

    async def wait_idle(self, timeout: float = 60) -> None:
        """Test helper: wait until the queue is drained."""
        await asyncio.wait_for(self.queue.join(), timeout)
