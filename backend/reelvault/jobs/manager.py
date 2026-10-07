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

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..locations import library_root, location_of
from ..media.encoding import EncoderRuntime
from ..media.ffmpeg import Canceled, ProcessHandle
from ..models import Job, Video, utcnow
from ..observability import record_job
from ..storage import check_budget, job_bytes, job_requirements, lock_budget

log = logging.getLogger("reelvault.jobs")


class JobContext:
    def __init__(self, manager: JobManager, job_id: str) -> None:
        self.manager = manager
        self.job_id = job_id
        self.handle = ProcessHandle()
        self.video_ids: list[str] = []
        self._last_flush = 0.0
        self.progress = 0.0
        self.message = ""
        self._rate_time = time.monotonic()
        self._rate_progress = 0.0
        self._last_progress = 0.0

    def reset_rate(self) -> None:
        self._rate_time = time.monotonic()
        self._rate_progress = self.progress
        self._last_progress = self.progress

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
        if self.progress < self._last_progress:
            self.reset_rate()
        self._last_progress = self.progress
        if now - self._last_flush < 0.5 and value < 1.0 and message is None:
            return
        self._last_flush = now
        with self.db() as db:
            job = db.get(Job, self.job_id)
            if job is not None:
                job.progress = self.progress
                job.message = self.message
                elapsed = now - self._rate_time
                advanced = self.progress - self._rate_progress
                job.eta_seconds = (
                    (1 - self.progress) * elapsed / advanced
                    if job.status == "running" and elapsed >= 3 and advanced >= 0.01
                    else None
                )
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
        "priority": job.priority,
        "eta_seconds": job.eta_seconds if job.status == "running" else None,
        "retry_of": job.retry_of,
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
        self.encoding = EncoderRuntime(settings.ffmpeg, device=settings.vaapi_device)
        self.sessionmaker = sessionmaker
        self.handlers = handlers
        self._wake = asyncio.Event()
        self.running: dict[str, JobContext] = {}
        self.subscribers: set[asyncio.Queue[str]] = set()
        self._tasks: list[asyncio.Task[None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        with self.sessionmaker() as db:
            for job in db.scalars(
                select(Job).where(
                    (Job.status == "running")
                    | ((Job.status == "paused") & Job.started_at.is_not(None))
                )
            ):
                job.status = "failed"
                job.error = "服务重启，任务被中断"
                job.finished_at = utcnow()
                job.eta_seconds = None
                record_job(db, job)
            db.commit()
        self._wake.set()
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

    def submit(
        self,
        db: Session,
        kind: str,
        params: dict[str, Any],
        video_ids: list[str],
        *,
        priority: int = 1,
    ) -> Job:
        return self.submit_many(db, [(kind, params, video_ids)], priority=priority)[0]

    def submit_many(
        self,
        db: Session,
        requests: list[tuple[str, dict[str, Any], list[str]]],
        *,
        priority: int = 1,
    ) -> list[Job]:
        lock_budget(db)
        budgeted = []
        for kind, params, ids in requests:
            if kind == "edit":
                output = dict(params.get("output", {}))
                if params["edit"]["op"] in ("animation", "extract_audio"):
                    target = "local"
                elif output.get("mode") == "replace" or (
                    params["edit"]["op"] == "embed_cover" and not params.get("history_replay")
                ):
                    source = db.get(Video, ids[0])
                    target = location_of(source.file_path) if source else "local"
                else:
                    target = output.get("storage_id") or self.settings.storage_default
                library_root(self.settings, target)
                params = {**params, "output": {**output, "storage_id": target}}
            estimate = job_bytes(db, kind, params, ids)
            budgeted.append(
                (
                    kind,
                    {
                        **params,
                        "storage_bytes": estimate,
                        "storage_plan": job_requirements(self.settings, kind, params, estimate),
                    }
                    if estimate
                    else params,
                    ids,
                )
            )
        requests = budgeted
        required = sum(params.get("storage_bytes", 0) for _, params, _ in requests)
        if required:
            plan: dict[str, int] = {}
            for _, params, _ in requests:
                for key, amount in params.get("storage_plan", {}).items():
                    plan[key] = plan.get(key, 0) + amount
            check_budget(db, self.settings, required, requirements=plan)
        jobs = [
            Job(kind=kind, params=params, video_ids=ids, priority=priority, message="排队中")
            for kind, params, ids in requests
        ]
        db.add_all(jobs)
        db.commit()
        for job in jobs:
            self.enqueue(job.id)
            self.publish(job)
        return jobs

    def submit_from_worker(
        self,
        kind: str,
        params: dict[str, Any],
        video_ids: list[str],
        *,
        reservation_from: str | None = None,
    ) -> str:
        with self.sessionmaker() as db:
            if reservation_from:
                lock_budget(db)
                parent = db.get(Job, reservation_from)
                video = db.get(Video, video_ids[0])
                if parent and video:
                    # Transfer remaining edit allowance to its derived-media job,
                    # rather than releasing it or reserving the same work twice.
                    remaining = max(0, int(parent.params.get("storage_bytes", 0)) - video.size)
                    parent.params = {**parent.params, "storage_bytes": 0, "storage_plan": {}}
                    job = Job(
                        kind=kind,
                        params={
                            **params,
                            "storage_bytes": remaining,
                            "storage_plan": {"local": remaining},
                        },
                        video_ids=video_ids,
                        message="排队中",
                    )
                    db.add(job)
                    db.commit()
                    self.enqueue(job.id)
                    self.publish(job)
                    return job.id
            return self.submit(db, kind, params, video_ids).id

    def enqueue(self, job_id: str) -> None:
        if self._loop is not None and self._loop.is_running():
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is self._loop:
                self._wake.set()
            else:
                self._loop.call_soon_threadsafe(self._wake.set)
        else:
            self._wake.set()

    def pending_count(self, *, include_paused: bool = False) -> int:
        with self.sessionmaker() as db:
            states = ["queued", "paused"] if include_paused else ["queued"]
            return int(
                db.scalar(select(func.count()).select_from(Job).where(Job.status.in_(states))) or 0
            )

    def describe(self, db: Session, job: Job) -> dict[str, Any]:
        result = job_to_dict(job)
        result["conflicting_jobs"] = (
            [
                other.id
                for other in db.scalars(
                    select(Job).where(
                        Job.id != job.id, Job.status.in_(["queued", "running", "paused"])
                    )
                )
                if set(other.video_ids).intersection(job.video_ids)
            ]
            if job.status in {"queued", "running", "paused"}
            else []
        )
        return result

    def pause(self, db: Session, job: Job) -> None:
        if job.status not in {"queued", "running"}:
            raise ValueError("只有排队或运行中的任务可以暂停")
        ctx = self.running.get(job.id)
        if job.status == "running" and ctx is None:
            raise ValueError("任务正在结束，请刷新后重试")
        if ctx:
            ctx.handle.pause()
        job.status = "paused"
        job.eta_seconds = None
        db.commit()
        self.publish(job)

    def resume(self, db: Session, job: Job) -> None:
        if job.status != "paused":
            raise ValueError("任务未暂停")
        ctx = self.running.get(job.id)
        if job.started_at and ctx is None:
            raise ValueError("编码进程已中断，请重试任务")
        job.status = "running" if ctx else "queued"
        db.commit()
        if ctx:
            ctx.reset_rate()
            ctx.handle.resume()
        self.enqueue(job.id)
        self.publish(job)

    def cancel(self, db: Session, job: Job) -> None:
        if job.status in {"queued", "paused"} and job.id not in self.running:
            job.status = "canceled"
            job.finished_at = utcnow()
            job.message = "已取消"
            record_job(db, job)
            db.commit()
            self.publish(job)
            self.enqueue(job.id)
        elif job.status in {"running", "paused"} and job.id in self.running:
            self.running[job.id].handle.cancel()

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self.subscribers.discard(q)

    def publish(self, job: Job) -> None:
        with self.sessionmaker() as db:
            payload = json.dumps(self.describe(db, job), ensure_ascii=False)

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
            # Selection and claiming are synchronous on the event loop.
            self._wake.clear()
            busy_ids = {vid for ctx in self.running.values() for vid in ctx.video_ids}
            with self.sessionmaker() as db:
                job_id = next(
                    (
                        job.id
                        for job in db.scalars(
                            select(Job)
                            .where(Job.status == "queued")
                            .order_by(Job.priority.desc(), Job.created_at, Job.id)
                        )
                        if not busy_ids.intersection(job.video_ids)
                    ),
                    None,
                )
            if job_id is None:
                await self._wake.wait()
                continue
            try:
                await self._run(job_id)
            except Exception:  # pragma: no cover - defensive
                log.exception("job %s crashed", job_id)
            finally:
                self._wake.set()

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
        ctx.video_ids = job.video_ids
        self.running[job_id] = ctx
        handler = self.handlers.get(job.kind)
        status, error, message = "succeeded", None, "完成"
        try:
            if handler is None:
                raise RuntimeError(f"未知任务类型 {job.kind}")
            with self.sessionmaker() as db:
                lock_budget(db)
                if job.kind == "edit":
                    # Metadata may have changed while waiting behind another edit.
                    # Old jobs always targeted the primary library before locations
                    # existed; freeze that choice when resuming an older queue.
                    output = dict(job.params.get("output", {}))
                    if not output.get("storage_id"):
                        output["storage_id"] = "local"
                    job.params = {**job.params, "output": output}
                    required = job_bytes(db, job.kind, job.params, list(job.video_ids))
                    plan = job_requirements(self.settings, job.kind, job.params, required)
                else:
                    required = (
                        job_bytes(db, job.kind, job.params, list(job.video_ids))
                        if "storage_bytes" not in job.params
                        else int(job.params["storage_bytes"])
                    )
                    plan = job.params.get("storage_plan") or job_requirements(
                        self.settings, job.kind, job.params, required
                    )
                if required:
                    check_budget(db, self.settings, required, exclude_job=job.id, requirements=plan)
                    job.params = {**job.params, "storage_bytes": required, "storage_plan": plan}
                    stored = db.get(Job, job.id)
                    if stored:
                        stored.params = {**stored.params, **job.params}
                    db.commit()
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
            if job.kind == "ingest" and status == "failed":
                for video_id in job.video_ids:
                    video = db.get(Video, video_id)
                    if video and video.status == "processing":
                        video.status, video.error = "error", (error or "处理失败")[:2000]
            final.status = status
            final.error = error
            final.message = message
            final.eta_seconds = None
            if status == "succeeded":
                final.progress = 1.0
            final.finished_at = utcnow()
            record_job(db, final)
            db.commit()
            self.publish(final)

    async def wait_idle(self, timeout: float = 60) -> None:
        """Wait for runnable and running work; queued paused tasks remain dormant."""

        async def wait() -> None:
            while self.running or self.pending_count():
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait(), timeout)
