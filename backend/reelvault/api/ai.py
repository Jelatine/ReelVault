from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..ai import FACE_MODEL_ID, AiParams, check_ai, current_analysis, prune_groups, revision
from ..auth import require_auth
from ..config import Settings
from ..db import get_db
from ..errors import APIError
from ..jobs.manager import JobManager
from ..library import abs_path
from ..media.scenes import source_signature
from ..models import (
    AiAnalysis,
    FaceGroup,
    FaceObservation,
    Job,
    Tag,
    VectorFrame,
    Video,
    VideoVectorIndex,
)
from ..storage import budget_transaction
from ..vision import MODEL_ID, VisionClient
from ..visual_search import current
from .deps import get_jobs, get_settings
from .scenes import ready_video

router = APIRouter(prefix="/api", tags=["ai"], dependencies=[Depends(require_auth)])
ID = r"^[a-f0-9]{32}$"


def active(db: Session, jobs: JobManager, video_id: str, *, all_indexes: bool = False) -> list[Job]:
    return [
        row
        for row in db.scalars(
            select(Job).where(
                Job.kind.in_(["ai_analyze", "vision_index"] if all_indexes else ["ai_analyze"]),
                or_(Job.status.in_(["queued", "running", "paused"]), Job.id.in_(jobs.running)),
            )
        )
        if video_id in row.video_ids
    ]


def submission(db: Session, settings: Settings, video_id: str, body: AiParams) -> dict:
    check_ai(settings, faces=body.faces)
    video = ready_video(db, video_id)
    index = db.get(VideoVectorIndex, video.id)
    if not index or not current(settings, video, index):
        raise APIError(409, "请先生成有效的画面索引", code="ai_index_required")
    if not db.scalar(select(VectorFrame.id).where(VectorFrame.video_id == video.id).limit(1)):
        raise APIError(409, "请先生成有效的画面索引", code="ai_index_required")
    count = (db.scalar(select(func.count()).select_from(FaceObservation)) or 0) if body.faces else 0
    return {
        **body.model_dump(exclude={"priority"}),
        "generation": index.generation,
        "storage_bytes": (128 * 1024 * 1024 + count * 1024 + index.frames * 16 * 4096)
        if body.faces
        else 16 * 1024 * 1024 + index.frames * 4096,
    }


@router.get("/ai/status")
def status(response: Response, settings: Settings = Depends(get_settings)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    available, face_ready, error = False, False, None
    if settings.ai_enabled and settings.vision_enabled:
        try:
            health = VisionClient(settings.vision_url, settings.vision_token).request("/health")
            available = health.get("ready") is True
            face_ready = (
                health.get("face_model") == FACE_MODEL_ID and health.get("face_dimension") == 128
            )
        except APIError as failure:
            error = failure.code
    return {
        "enabled": settings.ai_enabled and settings.vision_enabled,
        "available": available,
        "faces_enabled": settings.ai_faces_enabled,
        "faces_available": face_ready,
        "error": error,
    }


@router.get("/videos/{video_id}/ai-analysis")
def analysis_status(
    video_id: str,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    video = ready_video(db, video_id)
    index, analysis = db.get(VideoVectorIndex, video.id), db.get(AiAnalysis, video.id)
    valid = bool(index and analysis and current_analysis(settings, video, index, analysis))
    latest = db.scalar(
        select(Job)
        .where(Job.kind == "ai_analyze", Job.video_ids == [video.id])
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    suggestions = []
    if valid and analysis and index and settings.ai_enabled:
        suggestions = [
            {
                **row,
                "url": f"/videos/{video.id}?t={row['time']:.6f}",
                "thumbnail": (
                    f"/api/visual-search/frames/{row['frame_id']}?generation={index.generation}"
                ),
            }
            for row in analysis.suggestions
        ]
    return {
        "enabled": settings.ai_enabled and settings.vision_enabled,
        "faces_enabled": settings.ai_faces_enabled,
        "index_ready": bool(index and current(settings, video, index)),
        "has_analysis": analysis is not None,
        "stale": bool(analysis and not valid),
        "generation": index.generation if index else None,
        "analysis": {
            "suggestions": suggestions,
            "faces": db.scalar(
                select(func.count())
                .select_from(FaceObservation)
                .where(FaceObservation.video_id == video.id)
            ),
            "analyzed_at": analysis.analyzed_at,
        }
        if valid and analysis and settings.ai_enabled
        else None,
        "job": jobs.describe(db, latest) if latest else None,
    }


@router.post("/videos/{video_id}/ai-analysis")
def submit(
    video_id: str,
    body: AiParams,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict:
    check_ai(settings, faces=body.faces)
    with budget_transaction(db):
        ready_video(db, video_id)
        pending = active(db, jobs, video_id)
        if pending:
            return jobs.describe(db, pending[0])
        if active(db, jobs, video_id, all_indexes=True):
            raise APIError(409, "请先结束画面索引任务", code="vision_index_busy")
        params = submission(db, settings, video_id, body)
        return jobs.describe(
            db, jobs.submit(db, "ai_analyze", params, [video_id], priority=body.priority)
        )


@router.delete("/videos/{video_id}/ai-analysis")
def clear(
    video_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict:
    check_ai(settings)
    with budget_transaction(db):
        ready_video(db, video_id)
        if active(db, jobs, video_id, all_indexes=True):
            raise APIError(409, "请先结束 AI 分析任务", code="ai_busy")
        row = db.get(AiAnalysis, video_id)
        if row:
            db.delete(row)
            db.flush()
            prune_groups(db)
            revision(db, advance=True)
            db.commit()
        return {"ok": True}


class Accept(BaseModel):
    generation: str = Field(pattern=ID)
    names: list[str] = Field(min_length=1, max_length=10)


@router.post("/videos/{video_id}/ai-analysis/tags")
def accept(
    video_id: str,
    body: Accept,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    check_ai(settings)
    with budget_transaction(db):
        video = ready_video(db, video_id)
        index, analysis = db.get(VideoVectorIndex, video.id), db.get(AiAnalysis, video.id)
        if (
            not index
            or not analysis
            or body.generation != index.generation
            or not current_analysis(settings, video, index, analysis)
        ):
            raise APIError(409, "AI 分析已过期，请重新分析", code="ai_stale")
        if not set(body.names) <= {row["name"] for row in analysis.suggestions}:
            raise APIError(422, "请选择当前分析的候选标签", code="ai_tags_invalid")
        for name in dict.fromkeys(body.names):
            tag = db.scalar(select(Tag).where(Tag.name == name))
            if tag is None:
                tag = Tag(name=name)
                db.add(tag)
                db.flush()
            if tag not in video.tags:
                video.tags.append(tag)
        db.commit()
        return {"ok": True}


def face_rows(
    db: Session,
    settings: Settings,
    *,
    group_id: str | None = None,
    video_id: str | None = None,
    ignored: bool = False,
):
    query = (
        select(
            FaceObservation.id,
            FaceObservation.video_id,
            FaceObservation.frame_id,
            FaceObservation.box,
            FaceObservation.score,
            FaceObservation.group_id,
            FaceObservation.manual,
            FaceObservation.ignored,
            VectorFrame.timestamp.label("time"),
            Video.title,
            Video.width,
            Video.height,
            Video.file_path,
            VideoVectorIndex.generation,
            VideoVectorIndex.signature,
            FaceGroup.name.label("group_name"),
        )
        .join(VectorFrame, VectorFrame.id == FaceObservation.frame_id)
        .join(Video, Video.id == FaceObservation.video_id)
        .join(VideoVectorIndex, VideoVectorIndex.video_id == Video.id)
        .join(AiAnalysis, AiAnalysis.video_id == Video.id)
        .outerjoin(FaceGroup, FaceGroup.id == FaceObservation.group_id)
        .where(
            Video.status == "ready",
            Video.deleted_at.is_(None),
            VideoVectorIndex.model == MODEL_ID,
            AiAnalysis.model == MODEL_ID,
            AiAnalysis.face_model == FACE_MODEL_ID,
            AiAnalysis.generation == VideoVectorIndex.generation,
            FaceObservation.ignored == ignored,
        )
        .order_by(FaceObservation.video_id, VectorFrame.timestamp, FaceObservation.ordinal)
    )
    if group_id:
        query = query.where(FaceObservation.group_id == group_id)
    if video_id:
        query = query.where(FaceObservation.video_id == video_id)
    valid = {}
    for row in db.execute(query).mappings().yield_per(500):
        if row["video_id"] not in valid:
            try:
                valid[row["video_id"]] = (
                    source_signature(abs_path(settings, row["file_path"])) == row["signature"]
                )
            except (ValueError, OSError):
                valid[row["video_id"]] = False
        if valid[row["video_id"]]:
            yield {
                key: value for key, value in row.items() if key not in {"file_path", "signature"}
            } | {
                "thumbnail": (
                    f"/api/visual-search/frames/{row['frame_id']}?generation={row['generation']}"
                ),
                "url": f"/videos/{row['video_id']}?t={row['time']:.6f}",
            }


@router.get("/ai/faces")
def list_faces(
    response: Response,
    group_id: str | None = Query(None, pattern=ID),
    video_id: str | None = Query(None, pattern=ID),
    ignored: bool = False,
    page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    check_ai(settings, faces=True)
    response.headers["Cache-Control"] = "no-store"
    if video_id:
        ready_video(db, video_id)
    items, total = [], 0
    for row in face_rows(db, settings, group_id=group_id, video_id=video_id, ignored=ignored):
        if (page - 1) * page_size <= total < page * page_size:
            items.append(row)
        total += 1
    return {"items": items, "total": total, "page_size": page_size}


@router.get("/ai/face-groups")
def list_groups(
    response: Response,
    q: str = Query("", max_length=64),
    page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    check_ai(settings, faces=True)
    response.headers["Cache-Control"] = "no-store"
    counts: dict[str, int] = {}
    previews: dict[str, dict[str, Any]] = {}
    for row in face_rows(db, settings):
        group = row["group_id"]
        if group:
            counts[group] = counts.get(group, 0) + 1
            previews.setdefault(group, row)
    result = []
    for group in db.scalars(select(FaceGroup).order_by(FaceGroup.name, FaceGroup.id)):
        if (group.id in counts or group.name) and (
            not q or q.casefold() in group.name.casefold() or q.casefold() in group.id
        ):
            result.append(
                {
                    "id": group.id,
                    "name": group.name,
                    "count": counts.get(group.id, 0),
                    "preview": previews.get(group.id),
                }
            )
    return {
        "items": result[(page - 1) * page_size : page * page_size],
        "total": len(result),
        "page_size": page_size,
    }


class Name(BaseModel):
    name: str = Field(max_length=64)

    @field_validator("name")
    @classmethod
    def clean(cls, name: str) -> str:
        if any(ord(c) < 32 for c in name):
            raise ValueError("Name must not contain control characters")
        return name.strip()


def group(db, group_id):
    row = db.get(FaceGroup, group_id)
    if row is None:
        raise APIError(404, "人脸分组不存在", code="ai_group_missing")
    return row


@router.get("/ai/face-groups/{group_id}")
def group_detail(
    group_id: str,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    check_ai(settings, faces=True)
    response.headers["Cache-Control"] = "no-store"
    row = group(db, group_id)
    preview, count = None, 0
    for face in face_rows(db, settings, group_id=group_id):
        if preview is None:
            preview = face
        count += 1
    return {"id": row.id, "name": row.name, "count": count, "preview": preview}


@router.post("/ai/face-groups")
def create_group(
    body: Name, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict:
    check_ai(settings, faces=True)
    with budget_transaction(db):
        row = FaceGroup(name=body.name)
        db.add(row)
        db.flush()
        revision(db, advance=True)
        db.commit()
        return {"id": row.id, "name": row.name}


@router.patch("/ai/face-groups/{group_id}")
def rename_group(
    group_id: str,
    body: Name,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    check_ai(settings, faces=True)
    with budget_transaction(db):
        row = group(db, group_id)
        row.name = body.name
        revision(db, advance=True)
        db.commit()
        return {"ok": True}


class Target(BaseModel):
    target_id: str = Field(pattern=ID)


@router.post("/ai/face-groups/{group_id}/merge")
def merge_group(
    group_id: str,
    body: Target,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    check_ai(settings, faces=True)
    with budget_transaction(db):
        source, target = group(db, group_id), group(db, body.target_id)
        if source.id == target.id:
            raise APIError(422, "请选择另一个人脸分组", code="ai_group_target_invalid")
        db.execute(
            update(FaceObservation)
            .where(FaceObservation.group_id == source.id)
            .values(group_id=target.id, manual=True)
        )
        db.delete(source)
        revision(db, advance=True)
        db.commit()
        return {"ok": True}


@router.delete("/ai/face-groups/{group_id}")
def delete_group(
    group_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict:
    check_ai(settings, faces=True)
    with budget_transaction(db):
        source = group(db, group_id)
        db.execute(
            update(FaceObservation)
            .where(FaceObservation.group_id == source.id)
            .values(group_id=None, ignored=True, manual=True)
        )
        db.delete(source)
        revision(db, advance=True)
        db.commit()
        return {"ok": True}


class Assignment(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)
    group_id: str | None = Field(None, pattern=ID)
    name: str | None = Field(None, min_length=1, max_length=64)
    ignore: bool = False

    @model_validator(mode="after")
    def valid(self):
        import re

        if (
            len(set(self.ids)) != len(self.ids)
            or any(not re.fullmatch(ID, value) for value in self.ids)
            or sum((self.group_id is not None, self.name is not None, self.ignore)) != 1
        ):
            raise ValueError("Choose exactly one target and 1–100 distinct face identifiers")
        if self.name is not None:
            self.name = Name(name=self.name).name
            if not self.name:
                raise ValueError("Name must not be empty")
        return self


@router.post("/ai/faces/assign")
def assign(
    body: Assignment, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict:
    check_ai(settings, faces=True)
    with budget_transaction(db):
        rows = list(db.scalars(select(FaceObservation).where(FaceObservation.id.in_(body.ids))))
        if len(rows) != len(body.ids):
            raise APIError(404, "人脸记录不存在或已过期", code="ai_face_missing")
        for row in rows:
            video = ready_video(db, row.video_id)
            index, analysis = db.get(VideoVectorIndex, video.id), db.get(AiAnalysis, video.id)
            if (
                not index
                or not analysis
                or analysis.face_model != FACE_MODEL_ID
                or not current_analysis(settings, video, index, analysis)
            ):
                raise APIError(409, "人脸记录已过期，请重新分析", code="ai_stale")
        target = group(db, body.group_id) if body.group_id else None
        if body.name:
            target = FaceGroup(name=body.name)
            db.add(target)
            db.flush()
        for row in rows:
            row.group_id = target.id if target else None
            row.manual, row.ignored = True, body.ignore
        db.flush()
        prune_groups(db)
        revision(db, advance=True)
        db.commit()
        return {"ok": True}
