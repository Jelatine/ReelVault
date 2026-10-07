from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import Settings
from ..db import get_db
from ..errors import APIError
from ..models import AuditEvent
from ..observability import EVENTS, metrics
from .deps import get_settings

router = APIRouter(tags=["observability"])


@router.get("/api/system/audit", dependencies=[Depends(require_auth)])
def history(
    response: Response,
    event: str | None = None,
    before: int | None = Query(None, gt=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    if event is not None and event not in EVENTS:
        raise APIError(422, "审计事件类型无效", code="audit_event_invalid")
    statement = select(AuditEvent)
    if event:
        statement = statement.where(AuditEvent.event == event)
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    if before:
        statement = statement.where(AuditEvent.id < before)
    rows = list(db.scalars(statement.order_by(AuditEvent.id.desc()).limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [
            {
                "id": row.id,
                "event": row.event,
                "actor": row.actor,
                "peer": row.peer,
                "target": row.target,
                "details": row.details,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
        "total": total,
        "next_before": rows[-1].id if more else None,
        "retention_days": settings.audit_retention_days,
        "max_events": settings.audit_max_events,
        "metrics_enabled": bool(settings.metrics_token),
        "metrics_endpoint": "/api/system/metrics",
    }


@router.get("/api/system/metrics")
def scrape(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    if not settings.metrics_token:
        raise APIError(404, "指标采集尚未启用", code="metrics_disabled")
    supplied = request.headers.get("authorization", "")
    expected = "Bearer " + settings.metrics_token
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise APIError(401, "指标采集令牌无效", code="metrics_token_invalid")
    return Response(
        metrics(db, settings),
        headers={
            "Content-Type": "text/plain; version=0.0.4; charset=utf-8",
            "Cache-Control": "no-store",
        },
    )
