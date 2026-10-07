from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import Settings
from ..db import get_db
from ..errors import APIError
from ..jobs.manager import JobManager
from ..library import abs_path, video_to_dict
from ..media.duplicates import (
    ALGORITHM,
    SourceChanged,
    VisualPrint,
    content_hash,
    file_signature,
    visual_similarity,
)
from ..models import CollectionItem, DuplicateMatch, Job, Video, VideoFingerprint, utcnow
from .deps import get_jobs, get_settings
from .scenes import ready_video

router = APIRouter(
    prefix="/api/duplicates", tags=["duplicates"], dependencies=[Depends(require_auth)]
)
VideoID = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def preferred(videos: list[Video]) -> Video:
    return min(videos, key=lambda v: (-v.width * v.height, -v.bitrate, -v.size, v.created_at, v.id))


def valid_fingerprint(settings: Settings, video: Video, row: VideoFingerprint | None) -> bool:
    current = file_signature(abs_path(settings, video.file_path))
    return bool(
        row
        and row.algorithm == ALGORITHM
        and row.asset_version == video.asset_version
        and row.signature == current
    )


@router.get("")
def results(
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    kind: Literal["all", "exact", "similar"] = "all",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    groups: dict[str, list[Video]] = {}
    video_hashes: dict[str, str] = {}
    ready = unavailable = visual_failed = 0
    for video, row in db.execute(
        select(Video, VideoFingerprint)
        .outerjoin(VideoFingerprint, VideoFingerprint.video_id == Video.id)
        .where(Video.deleted_at.is_(None), Video.status == "ready")
        .order_by(Video.id)
    ):
        ready += 1
        try:
            if valid_fingerprint(settings, video, row):
                assert row is not None
                groups.setdefault(row.sha256, []).append(video)
                video_hashes[video.id] = row.sha256
                visual_failed += int(row.visual is None)
        except OSError:
            unavailable += 1
    offset = (page - 1) * page_size
    count = 0
    items: list[dict[str, Any]] = []

    def append(group_kind: str, key: str, videos: list[Video], score: float) -> None:
        nonlocal count
        if offset <= count < offset + page_size:
            items.append(
                {
                    "kind": group_kind,
                    "key": key,
                    "score": score,
                    "keep_id": preferred(videos).id,
                    "videos": [
                        {
                            **video_to_dict(v),
                            "sha256": video_hashes[v.id],
                        }
                        for v in videos
                    ],
                }
            )
        count += 1

    if kind != "similar":
        for digest, videos in sorted(groups.items()):
            if len(videos) > 1:
                append("exact", digest, videos, 1)
    if kind != "exact":
        for match in db.scalars(
            select(DuplicateMatch)
            .where(DuplicateMatch.algorithm == ALGORITHM)
            .order_by(
                DuplicateMatch.score.desc(), DuplicateMatch.left_hash, DuplicateMatch.right_hash
            )
            .execution_options(yield_per=1000)
        ):
            if match.left_hash in groups and match.right_hash in groups:
                append(
                    "similar",
                    f"{match.left_hash}:{match.right_hash}",
                    [preferred(groups[match.left_hash]), preferred(groups[match.right_hash])],
                    match.score,
                )
    latest = db.scalar(
        select(Job)
        .where(Job.kind == "duplicates")
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )
    fingerprinted = sum(len(v) for v in groups.values())
    return {
        "items": items,
        "total": count,
        "page": page,
        "page_size": page_size,
        "ready": ready,
        "fingerprinted": fingerprinted,
        "unscanned": ready - fingerprinted,
        "unavailable": unavailable,
        "visual_failed": visual_failed,
        "job": jobs.describe(db, latest) if latest else None,
    }


@router.post("/scan")
def scan(db: Session = Depends(get_db), jobs: JobManager = Depends(get_jobs)) -> dict[str, Any]:
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    active = db.scalar(
        select(Job).where(Job.kind == "duplicates", Job.status.in_(["queued", "running", "paused"]))
    )
    if active:
        return jobs.describe(db, active)
    ids = list(
        db.scalars(
            select(Video.id)
            .where(Video.status == "ready", Video.deleted_at.is_(None))
            .order_by(Video.id)
        )
    )
    job = jobs.submit(db, "duplicates", {}, ids)
    return jobs.describe(db, job)


class ResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keep_id: VideoID
    remove_ids: list[VideoID] = Field(min_length=1, max_length=1000)
    expected_hashes: dict[VideoID, Digest]
    kind: Literal["exact", "similar"]
    merge: bool = True

    @model_validator(mode="after")
    def validate_ids(self) -> ResolveBody:
        if self.keep_id in self.remove_ids or len(set(self.remove_ids)) != len(self.remove_ids):
            raise ValueError("Choose distinct videos to retain and remove")
        if set(self.expected_hashes) != {self.keep_id, *self.remove_ids}:
            raise ValueError("Provide the displayed content hash for every selected video")
        return self


def ensure_idle(db: Session, ids: set[str]) -> None:
    for job in db.scalars(select(Job).where(Job.status.in_(["queued", "running", "paused"]))):
        if ids.intersection(job.video_ids):
            raise APIError(
                409, "所选视频仍有未完成任务，请完成或取消后再处理", code="duplicate_videos_busy"
            )


def merge_information(db: Session, target: Video, sources: list[Video]) -> list[str]:
    tags = {tag.id: tag for tag in target.tags}
    custom = dict(target.custom_fields)
    skipped: set[str] = set()
    for source in sources:
        tags.update({tag.id: tag for tag in source.tags})
        target.rating = max(target.rating, source.rating)
        target.favorite = target.favorite or source.favorite
        for key, value in source.custom_fields.items():
            if key in custom:
                if custom[key] != value:
                    skipped.add(key)
            elif len(custom) < 50:
                custom[key] = value
            else:
                skipped.add(key)
        for item in db.scalars(select(CollectionItem).where(CollectionItem.video_id == source.id)):
            if db.get(CollectionItem, (item.collection_id, target.id)) is None:
                db.add(
                    CollectionItem(
                        collection_id=item.collection_id, video_id=target.id, position=item.position
                    )
                )
                db.flush()
    target.tags, target.custom_fields, target.updated_at = list(tags.values()), custom, utcnow()
    return sorted(skipped)


@router.post("/resolve")
async def resolve(
    body: ResolveBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    ids = {body.keep_id, *body.remove_ids}
    ensure_idle(db, ids)
    snapshots: dict[str, tuple[int, str]] = {}
    signatures: dict[str, list[Any]] = {}
    try:
        for video_id in [body.keep_id, *body.remove_ids]:
            video = ready_video(db, video_id)
            row = db.get(VideoFingerprint, video_id)
            if (
                not valid_fingerprint(settings, video, row)
                or row is None
                or row.sha256 != body.expected_hashes[video_id]
            ):
                raise APIError(409, "检测结果已过期，请重新扫描", code="duplicate_result_stale")
            snapshots[video_id] = video.asset_version, video.file_path
        db.rollback()
        # Re-read complete files before changing membership; cached hashes alone are insufficient.
        for video_id, (_, path) in snapshots.items():
            digest, signature = await content_hash(abs_path(settings, path))
            if digest != body.expected_hashes[video_id]:
                raise SourceChanged("Content changed since the displayed result")
            signatures[video_id] = signature
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        ensure_idle(db, ids)
        videos: dict[str, Video] = {}
        prints: dict[str, VideoFingerprint] = {}
        for video_id in snapshots:
            video = ready_video(db, video_id)
            row = db.get(VideoFingerprint, video_id)
            if (
                snapshots[video_id] != (video.asset_version, video.file_path)
                or file_signature(abs_path(settings, video.file_path)) != signatures[video_id]
                or not valid_fingerprint(settings, video, row)
                or row is None
                or row.sha256 != body.expected_hashes[video_id]
            ):
                raise SourceChanged("Content changed while checking the result")
            videos[video_id], prints[video_id] = video, row
        target_print = prints[body.keep_id]
        for video_id in body.remove_ids:
            other = prints[video_id]
            same = other.sha256 == target_print.sha256
            similar = (
                body.kind == "similar"
                and target_print.visual is not None
                and other.visual is not None
                and visual_similarity(
                    VisualPrint.from_dict(target_print.visual), VisualPrint.from_dict(other.visual)
                )
                is not None
            )
            if not same and not similar:
                raise APIError(409, "所选视频不是有效的重复或相似匹配", code="duplicate_not_match")
        target = videos[body.keep_id]
        sources = [videos[vid] for vid in body.remove_ids]
        skipped = merge_information(db, target, sources) if body.merge else []
        now = utcnow()
        for source in sources:
            source.deleted_at = now
        db.commit()
        return {
            "kept": video_to_dict(target),
            "trashed": body.remove_ids,
            "skipped_custom_fields": skipped,
        }
    except (OSError, SourceChanged) as error:
        db.rollback()
        raise APIError(
            409, "视频文件已变化或不可用，请重新扫描", code="duplicate_result_stale"
        ) from error
