"""Transactional audit history and monotonic, bounded-cardinality job metrics."""

from __future__ import annotations

import json
import math
from datetime import timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .config import Settings
from .models import AuditEvent, Job, JobMetric, utcnow
from .storage import snapshot

KINDS = (
    "ingest",
    "edit",
    "hls",
    "scenes",
    "duplicates",
    "link_import",
    "playable",
    "transcribe",
    "vision_index",
    "ai_analyze",
    "other",
)
STATES = ("queued", "running", "paused", "succeeded", "failed", "canceled")
TERMINAL = STATES[3:]
BUCKETS = (1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600)
EVENTS = (
    "login_success",
    "login_failure",
    "login_limited",
    "logout",
    "video_trash",
    "video_restore",
    "video_purge",
    "trash_retention",
    "upgrade_started",
    "upgrade_applied",
    "upgrade_failed",
    "upgrade_restarted",
)


def audit(
    db: Session,
    settings: Settings,
    event: str,
    *,
    actor: str = "system",
    peer: str = "",
    target: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    if event not in EVENTS:
        raise ValueError("Unknown audit event")
    db.add(
        AuditEvent(
            event=event,
            actor=actor[:64],
            peer=peer[:64],
            target=target[:64] if target else None,
            details=details or {},
        )
    )
    db.flush()
    prune_audit(db, settings)


def audit_request(db: Session, request: Request, event: str, target: str | None = None) -> None:
    auth = request.state.auth
    audit(
        db,
        request.app.state.settings,
        event,
        actor=auth.username,
        peer=request.client.host if request.client else "",
        target=target,
    )


def prune_audit(db: Session, settings: Settings) -> None:
    db.execute(
        delete(AuditEvent).where(
            AuditEvent.created_at < utcnow() - timedelta(days=settings.audit_retention_days)
        )
    )
    # A row cap bounds growth even during repeated failed authentication.
    threshold = db.scalar(
        select(AuditEvent.id)
        .order_by(AuditEvent.id.desc())
        .offset(settings.audit_max_events - 1)
        .limit(1)
    )
    if threshold is not None:
        db.execute(delete(AuditEvent).where(AuditEvent.id < threshold))


def record_job(db: Session, job: Job) -> None:
    if job.status not in TERMINAL:
        return
    # Claim exactly once under SQLite's writer lock; counters and the terminal job
    # state share a commit, including concurrent worker completions and recovery.
    claimed = db.execute(
        update(Job)
        .where(Job.id == job.id, Job.metrics_recorded.is_(False))
        .values(metrics_recorded=True)
        .returning(Job.id)
    )
    if claimed.scalar_one_or_none() is None:
        return
    kind = job.kind if job.kind in KINDS else "other"
    metric = db.get(JobMetric, (kind, job.status))
    if metric is None:
        metric = JobMetric(
            kind=kind,
            status=job.status,
            completed=0,
            duration_count=0,
            duration_sum=0,
            buckets=[0] * len(BUCKETS),
        )
        db.add(metric)
    metric.completed += 1
    if job.started_at is None or job.finished_at is None:
        return
    seconds = (job.finished_at - job.started_at).total_seconds()
    if not math.isfinite(seconds) or seconds < 0:
        return
    metric.duration_count += 1
    metric.duration_sum += seconds
    metric.buckets = [
        n + int(seconds <= limit) for n, limit in zip(metric.buckets, BUCKETS, strict=True)
    ]


def label(values: dict[str, str]) -> str:
    return (
        "{"
        + ",".join(
            f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in values.items()
        )
        + "}"
    )


def metrics(db: Session, settings: Settings) -> str:
    lines: list[str] = []

    def family(name: str, kind: str, help_text: str) -> None:
        lines.extend([f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"])

    def sample(name: str, value: float | int, **labels: str) -> None:
        lines.append(f"{name}{label(labels) if labels else ''} {value}")

    family("reelvault_jobs", "gauge", "Current jobs by kind and state.")
    counts: dict[tuple[str, str], int] = {}
    for kind, state, count in db.execute(
        select(Job.kind, Job.status, func.count()).group_by(Job.kind, Job.status)
    ):
        pair = (kind if kind in KINDS else "other", state)
        counts[pair] = counts.get(pair, 0) + count
    for kind in KINDS:
        for state in STATES:
            sample("reelvault_jobs", counts.get((kind, state), 0), kind=kind, status=state)
    family(
        "reelvault_jobs_completed_total",
        "counter",
        "Terminal transitions since metrics installation.",
    )
    totals = {(row.kind, row.status): row for row in db.scalars(select(JobMetric))}
    for kind in KINDS:
        for state in TERMINAL:
            row = totals.get((kind, state))
            sample(
                "reelvault_jobs_completed_total",
                row.completed if row else 0,
                kind=kind,
                status=state,
            )
    name = "reelvault_job_duration_seconds"
    family(
        name,
        "histogram",
        "Wall time from job start to completion, including pauses; excludes queued cancellations.",
    )
    for kind in KINDS:
        for state in TERMINAL:
            row = totals.get((kind, state))
            for index, bound in enumerate(BUCKETS):
                sample(
                    name + "_bucket",
                    row.buckets[index] if row else 0,
                    kind=kind,
                    status=state,
                    le=str(bound),
                )
            sample(
                name + "_bucket",
                row.duration_count if row else 0,
                kind=kind,
                status=state,
                le="+Inf",
            )
            sample(name + "_count", row.duration_count if row else 0, kind=kind, status=state)
            sample(name + "_sum", row.duration_sum if row else 0, kind=kind, status=state)
    storage = snapshot(db, settings)
    for metric_name, key, description in (
        ("reelvault_storage_online", "available", "Storage location availability."),
        (
            "reelvault_storage_free_bytes",
            "free",
            "Free filesystem bytes; locations on one filesystem share capacity.",
        ),
        (
            "reelvault_storage_total_bytes",
            "total",
            "Total filesystem bytes; locations on one filesystem share capacity.",
        ),
        (
            "reelvault_storage_reserved_bytes",
            "reserved_bytes",
            "Outstanding upload and job reservation bytes.",
        ),
        ("reelvault_storage_available_bytes", "available_bytes", "Free bytes after reservations."),
    ):
        family(metric_name, "gauge", description)
        for location in storage["locations"]:
            value = location.get(key)
            if value is not None:
                sample(metric_name, int(value), location=location["id"])
    return "\n".join(lines) + "\n"
