from __future__ import annotations

import mimetypes
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import aiofiles
from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import case, column, func, literal, literal_column, or_, select, table, text
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import Settings
from ..db import get_db
from ..errors import APIError
from ..jobs.manager import JobManager
from ..library import (
    VIDEO_EXTENSIONS,
    abs_path,
    delete_video_files,
    derived_dir,
    folder_exists,
    stem_of,
    store_file,
    video_to_dict,
)
from ..locations import library_root
from ..media import derive
from ..media.ffmpeg import FFmpegError, ffmpeg_args, run_command
from ..media.probe import probe
from ..media.timing import timing_index
from ..metadata import MetadataPatch, update_metadata
from ..models import Folder, Tag, Upload, Video, new_id, utcnow
from ..observability import audit_request
from ..pinyin_search import normalize_pinyin_query
from ..search_syntax import parse_search
from ..storage import check_budget, lock_budget, upload_bytes, upload_requirements
from .deps import FiniteNumber, get_jobs, get_settings

router = APIRouter(prefix="/api", tags=["videos"], dependencies=[Depends(require_auth)])


def get_video(db: Session, video_id: str, *, allow_deleted: bool = False) -> Video:
    video = db.get(Video, video_id)
    if video is None or (video.deleted_at is not None and not allow_deleted):
        raise APIError(status.HTTP_404_NOT_FOUND, "视频不存在", code="video_not_found")
    return video


@router.get("/videos/{video_id}/playlist")
def folder_playlist(video_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    video = get_video(db, video_id)
    folder = db.get(Folder, video.folder_id) if video.folder_id is not None else None
    items = db.scalars(
        select(Video)
        .where(
            Video.folder_id == video.folder_id, Video.status == "ready", Video.deleted_at.is_(None)
        )
        .order_by(Video.created_at, Video.id)
    ).all()
    return {"name": folder.name if folder else "未分类", "items": [video_to_dict(v) for v in items]}


def set_tags(db: Session, video: Video, names: list[str]) -> None:
    clean = sorted({n.strip()[:64] for n in names if n.strip()})
    tags = []
    for name in clean:
        tag = db.scalar(select(Tag).where(Tag.name == name))
        if tag is None:
            tag = Tag(name=name)
            db.add(tag)
        tags.append(tag)
    video.tags = tags


# ------------------------------------------------------------------ uploads


class UploadInit(BaseModel):
    storage_id: str | None = Field(default=None, pattern=r"^(local|[a-f0-9]{32})$")
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    folder_id: int | None = None
    relative_path: str | None = Field(default=None, max_length=2048)
    tags: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_path(self) -> UploadInit:
        parts = (self.relative_path or self.filename).split("/")
        if (
            len(parts) > 33
            or any(
                not p.strip()
                or p in (".", "..")
                or len(p) > 255
                or any(c in p for c in ("\\", "\x00", ":"))
                for p in parts
            )
            or parts[-1] != self.filename
            or "/" in self.filename
        ):
            raise ValueError("无效的上传目录或文件名")
        self.tags = sorted({t.strip()[:64] for t in self.tags if t.strip()})
        return self


def _upload_dict(u: Upload, settings: Settings) -> dict[str, Any]:
    return {
        "id": u.id,
        "filename": u.filename,
        "size": u.size,
        "received": u.received,
        "storage_id": u.storage_id,
        "chunk_size": settings.upload_chunk_size,
        "folder_id": u.folder_id,
        "relative_path": u.relative_path,
        "tags": u.tags,
    }


def _upload_path(settings: Settings, upload_id: str) -> Path:
    return settings.tmp_dir / f"upload-{upload_id}.part"


@router.post("/uploads")
def init_upload(
    body: UploadInit,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if Path(body.filename).suffix.lower() not in VIDEO_EXTENSIONS:
        raise APIError(
            status.HTTP_400_BAD_REQUEST, "不支持的视频格式", code="video_format_unsupported"
        )
    if not folder_exists(db, body.folder_id):
        raise APIError(status.HTTP_400_BAD_REQUEST, "文件夹不存在", code="folder_not_found")
    lock_budget(db)
    storage_id = body.storage_id or settings.storage_default
    library_root(settings, storage_id)
    check_budget(
        db,
        settings,
        upload_bytes(body.size),
        requirements=upload_requirements(body.size, storage_id),
    )
    upload = Upload(
        storage_id=storage_id,
        filename=body.filename,
        size=body.size,
        folder_id=body.folder_id,
        relative_path=body.relative_path,
        tags=body.tags,
    )
    db.add(upload)
    db.commit()
    _upload_path(settings, upload.id).touch()
    return _upload_dict(upload, settings)


@router.get("/uploads/{upload_id}")
def get_upload(
    upload_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    upload = db.get(Upload, upload_id)
    if upload is None:
        raise APIError(status.HTTP_404_NOT_FOUND, "上传不存在", code="upload_not_found")
    return _upload_dict(upload, settings)


@router.put("/uploads/{upload_id}")
async def upload_chunk(
    upload_id: str,
    request: Request,
    offset: int = Query(ge=0),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    upload = db.get(Upload, upload_id)
    if upload is None:
        raise APIError(status.HTTP_404_NOT_FOUND, "上传不存在", code="upload_not_found")
    # Re-sending from an earlier offset is fine (e.g. the response to a chunk was lost).
    if offset > upload.received:
        raise APIError(
            status.HTTP_409_CONFLICT,
            {"message": "偏移量不匹配", "received": upload.received},
            code="upload_offset_mismatch",
            params={"received": upload.received},
        )
    path = _upload_path(settings, upload_id)
    received_bytes = path.stat().st_size if path.exists() else 0
    plan = upload_requirements(upload.size, upload.storage_id)
    plan["local"] = max(0, plan["local"] - received_bytes)
    check_budget(db, settings, plan["local"], exclude_upload=upload.id, requirements=plan)
    written = 0
    async with aiofiles.open(path, "r+b" if path.exists() else "wb") as f:
        await f.seek(offset)
        async for chunk in request.stream():
            if offset + written + len(chunk) > upload.size:
                raise APIError(
                    status.HTTP_400_BAD_REQUEST, "数据超出文件大小", code="upload_size_exceeded"
                )
            await f.write(chunk)
            written += len(chunk)
        await f.truncate(offset + written)
    upload.received = offset + written
    db.commit()
    return _upload_dict(upload, settings)


@router.post("/uploads/{upload_id}/complete")
def complete_upload(
    upload_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict[str, Any]:
    # Serialize folder creation and completion across worker threads/processes.
    db.execute(text("BEGIN IMMEDIATE"))
    upload = db.get(Upload, upload_id)
    if upload is None:
        raise APIError(status.HTTP_404_NOT_FOUND, "上传不存在", code="upload_not_found")
    if upload.received != upload.size:
        raise APIError(status.HTTP_400_BAD_REQUEST, "文件尚未上传完成", code="upload_incomplete")
    if not folder_exists(db, upload.folder_id):
        raise APIError(
            409, "目标文件夹已删除，请取消并重新选择上传位置", code="upload_folder_deleted"
        )
    folder_id = upload.folder_id
    for name in (upload.relative_path or upload.filename).split("/")[:-1]:
        folder = db.scalar(select(Folder).where(Folder.name == name, Folder.parent_id == folder_id))
        if folder is None:
            folder = Folder(name=name, parent_id=folder_id)
            db.add(folder)
            db.flush()
        folder_id = folder.id
    video = store_file(
        db,
        settings,
        _upload_path(settings, upload_id),
        title=stem_of(upload.filename),
        original_name=upload.filename,
        folder_id=folder_id,
        commit=False,
        storage_id=upload.storage_id,
    )
    try:
        set_tags(db, video, upload.tags)
        db.delete(upload)
        db.flush()
        jobs.submit(
            db, "ingest", {"storage_bytes": upload_bytes(upload.size) - upload.size}, [video.id]
        )
    except Exception:
        db.rollback()
        shutil.move(str(abs_path(settings, video.file_path)), _upload_path(settings, upload_id))
        raise
    return video_to_dict(video)


@router.delete("/uploads/{upload_id}")
def abort_upload(
    upload_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, bool]:
    upload = db.get(Upload, upload_id)
    if upload is not None:
        db.delete(upload)
        db.commit()
    _upload_path(settings, upload_id).unlink(missing_ok=True)
    return {"ok": True}


# ------------------------------------------------------------------ listing / CRUD

SortKey = Literal[
    "created", "title", "size", "duration", "updated", "rating", "favorite", "relevance", "captured"
]
SORT_COLUMNS = {
    "created": Video.created_at,
    "title": Video.title,
    "size": Video.size,
    "duration": Video.duration,
    "updated": Video.updated_at,
    "rating": Video.rating,
    "favorite": Video.favorite,
    "captured": func.coalesce(Video.captured_at, Video.created_at),
    "relevance": Video.created_at,
}


@router.get("/videos")
def list_videos(
    q: str = "",
    folder: str = "all",
    tag: str | None = None,
    trash: bool = False,
    rating_min: int = Query(0, ge=0, le=5),
    favorite: bool | None = None,
    duration_min: FiniteNumber | None = Query(None, ge=0),
    duration_max: FiniteNumber | None = Query(None, ge=0),
    size_min: int | None = Query(None, ge=0),
    size_max: int | None = Query(None, ge=0),
    resolution: Literal["4k", "1080p", "720p", "portrait", "landscape"] | None = None,
    codec: str | None = None,
    format: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    captured_after: datetime | None = None,
    captured_before: datetime | None = None,
    auto: str | None = None,
    include_children: bool = False,
    sort: SortKey | None = None,
    order: Literal["asc", "desc"] = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(48, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    sort = sort or ("relevance" if q.strip() else "captured")
    parsed_search = parse_search(q)
    terms = parsed_search.terms
    stmt = select(Video)
    stmt = stmt.where(*parsed_search.conditions)
    stmt = stmt.where(Video.deleted_at.is_not(None) if trash else Video.deleted_at.is_(None))
    stmt = stmt.where(Video.rating >= rating_min)
    if auto is not None:
        from ..grouping import group_condition

        stmt = stmt.where(
            Video.status == "ready", Video.deleted_at.is_(None), group_condition(auto)
        )
    if favorite is not None:
        stmt = stmt.where(Video.favorite == favorite)
    ranges: list[tuple[Any, Any, Any]] = [
        (duration_min, duration_max, Video.duration),
        (size_min, size_max, Video.size),
        (created_after, created_before, Video.created_at),
        (captured_after, captured_before, Video.captured_at),
    ]
    for minimum, maximum, filter_col in ranges:
        if isinstance(minimum, datetime):
            minimum = (
                minimum.replace(tzinfo=UTC) if minimum.tzinfo is None else minimum.astimezone(UTC)
            )
        if isinstance(maximum, datetime):
            maximum = (
                maximum.replace(tzinfo=UTC) if maximum.tzinfo is None else maximum.astimezone(UTC)
            )
        if minimum is not None and maximum is not None and minimum > maximum:
            raise APIError(400, "筛选范围的下限不能超过上限", code="filter_range_invalid")
        if minimum is not None:
            stmt = stmt.where(filter_col >= minimum)
        if maximum is not None:
            stmt = stmt.where(filter_col <= maximum)
    if resolution in {"4k", "1080p", "720p"}:
        height = {"4k": 2160, "1080p": 1080, "720p": 720}[resolution]
        # Compare the shorter side to also recognize vertical UHD/HD video.
        stmt = stmt.where(func.min(Video.width, Video.height) >= height)
    elif resolution == "portrait":
        stmt = stmt.where(Video.height > Video.width)
    elif resolution == "landscape":
        stmt = stmt.where(Video.width > Video.height)
    if codec:
        stmt = stmt.where(Video.video_codec == codec)
    if format:
        stmt = stmt.where(Video.container == format)
    has_match = False
    short_rank: Any = None
    pinyin_match: Any = literal(False)
    if terms:
        fields = ("title", "description", "original_name", "tags")
        pinyin_fields = ("title_pinyin", "title_initials")
        search = table(
            "video_search",
            *(column(c) for c in ("video_id", *fields, *pinyin_fields)),
        )
        stmt = stmt.join(search, search.c.video_id == Video.id)
        short_rank = sum(
            case((func.instr(func.lower(search.c[c]), term.lower()) > 0, weight), else_=0)
            for term in terms
            for c, weight in (("title", 8), ("tags", 4), ("original_name", 3), ("description", 1))
        )
        normalized = [(term, normalize_pinyin_query(term)) for term in terms]
        pinyin_checks = [
            or_(*(func.instr(search.c[c], phonetic) > 0 for c in pinyin_fields))
            & ~or_(
                *(
                    func.instr(func.reelvault_casefold(search.c[c]), term.casefold()) > 0
                    for c in fields
                )
            )
            for term, phonetic in normalized
            if phonetic is not None
        ]
        if pinyin_checks:
            pinyin_match = or_(*pinyin_checks)
        short_rank += sum(
            case((func.instr(search.c[c], phonetic) > 0, weight), else_=0)
            for _, phonetic in normalized
            if phonetic is not None
            for c, weight in (("title_pinyin", 2), ("title_initials", 1))
        )
        long_terms = [
            (term, phonetic)
            for term, phonetic in normalized
            if len(term) >= 3 and (phonetic is None or len(phonetic) >= 3)
        ]
        if long_terms:
            expressions = []
            for term, phonetic in long_terms:
                quoted_term = '"' + term.replace('"', '""') + '"'
                original = "{title description original_name tags}:" + quoted_term
                if phonetic is not None:
                    original = (
                        "(" + original + ' OR {title_pinyin title_initials}:"' + phonetic + '")'
                    )
                expressions.append(original)
            match = " AND ".join(expressions)
            stmt = stmt.where(text("video_search MATCH :search_query")).params(search_query=match)
            has_match = True
        # Trigram MATCH cannot match one/two-character words. Search the FTS
        # document itself for those, retaining Chinese short-word support.
        for term, phonetic in (item for item in normalized if item not in long_terms):
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            options = [search.c[c].like(f"%{escaped}%", escape="\\") for c in fields]
            if phonetic is not None:
                options.extend(search.c[c].like(f"%{phonetic}%") for c in pinyin_fields)
            stmt = stmt.where(or_(*options))
    if folder == "root":
        stmt = stmt.where(Video.folder_id.is_(None))
    elif folder != "all":
        try:
            folder_id = int(folder)
            if include_children:
                descendants = select(Folder.id).where(Folder.id == folder_id).cte(recursive=True)
                descendants = descendants.union_all(
                    select(Folder.id).join(descendants, Folder.parent_id == descendants.c.id)
                )
                stmt = stmt.where(Video.folder_id.in_(select(descendants.c.id)))
            else:
                stmt = stmt.where(Video.folder_id == folder_id)
        except ValueError as e:
            raise APIError(
                status.HTTP_400_BAD_REQUEST, "无效的文件夹", code="folder_not_found"
            ) from e
    if tag:
        stmt = stmt.where(Video.tags.any(Tag.name == tag))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    col = SORT_COLUMNS[sort]
    if sort == "relevance" and has_match:
        stmt = stmt.order_by(
            pinyin_match.asc(), literal_column("bm25(video_search, 0, 8, 1, 3, 4, 2, 1)"), Video.id
        )
    elif sort == "relevance" and short_rank is not None:
        stmt = stmt.order_by(pinyin_match.asc(), short_rank.desc(), Video.id)
    else:
        stmt = stmt.order_by(col.asc() if order == "asc" else col.desc(), Video.id)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = []
    if has_match:
        excerpts = stmt.add_columns(
            literal_column("snippet(video_search, -1, '', '', '…', 32)"), pinyin_match
        )
        for video, excerpt, phonetic in db.execute(excerpts):
            items.append(
                {
                    **video_to_dict(video),
                    "search_excerpt": video.title if phonetic else excerpt,
                    "search_pinyin": bool(phonetic),
                }
            )
    else:
        for video, phonetic in db.execute(stmt.add_columns(pinyin_match)):
            item = video_to_dict(video)
            if phonetic:
                item.update(search_excerpt=video.title, search_pinyin=True)
            if terms:
                excerpt_fields = [
                    video.title,
                    " ".join(t.name for t in video.tags),
                    video.original_name,
                    video.description,
                ]
                for value in excerpt_fields:
                    matches = [value.lower().find(term.lower()) for term in terms]
                    positions = [position for position in matches if position >= 0]
                    if positions:
                        start = max(0, min(positions) - 30)
                        item["search_excerpt"] = ("…" if start else "") + value[start : start + 120]
                        break
            items.append(item)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "search_terms": terms,
    }


@router.get("/videos/{video_id}")
def video_detail(video_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return video_to_dict(get_video(db, video_id, allow_deleted=True))


class VideoPatch(BaseModel):
    rating: int | None = Field(None, ge=0, le=5)
    favorite: bool | None = None
    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=10000)
    folder_id: int | None = None
    move: bool = False  # folder_id is applied only when true (so null can mean "root")
    tags: list[str] | None = None


@router.patch("/videos/{video_id}/metadata")
def edit_metadata(
    video_id: str, body: MetadataPatch, db: Session = Depends(get_db)
) -> dict[str, Any]:
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    video = get_video(db, video_id)
    update_metadata(video, body)
    video.updated_at = utcnow()
    db.commit()
    return video_to_dict(video)


@router.patch("/videos/{video_id}")
def update_video(video_id: str, body: VideoPatch, db: Session = Depends(get_db)) -> dict[str, Any]:
    video = get_video(db, video_id)
    if body.rating is not None:
        video.rating = body.rating
    if body.favorite is not None:
        video.favorite = body.favorite
    if body.title is not None:
        video.title = body.title.strip()
    if body.description is not None:
        video.description = body.description
    if body.move:
        if not folder_exists(db, body.folder_id):
            raise APIError(status.HTTP_400_BAD_REQUEST, "文件夹不存在", code="folder_not_found")
        video.folder_id = body.folder_id
    if body.tags is not None:
        set_tags(db, video, body.tags)
    video.updated_at = utcnow()
    db.commit()
    return video_to_dict(video)


def _purge(db: Session, settings: Settings, video: Video) -> None:
    delete_video_files(settings, video)
    db.delete(video)


@router.delete("/videos/{video_id}")
def delete_video(
    video_id: str,
    request: Request,
    permanent: bool = False,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, bool]:
    video = get_video(db, video_id, allow_deleted=True)
    if permanent or video.deleted_at is not None:
        audit_request(db, request, "video_purge", video.id)
        _purge(db, settings, video)
    else:
        audit_request(db, request, "video_trash", video.id)
        video.deleted_at = utcnow()
    db.commit()
    return {"ok": True}


@router.post("/videos/{video_id}/restore")
def restore_video(video_id: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    video = get_video(db, video_id, allow_deleted=True)
    audit_request(db, request, "video_restore", video.id)
    video.deleted_at = None
    if not folder_exists(db, video.folder_id):
        video.folder_id = None
    db.commit()
    return video_to_dict(video)


@router.post("/trash/empty")
def empty_trash(
    request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, int]:
    videos = db.scalars(select(Video).where(Video.deleted_at.is_not(None))).all()
    for v in videos:
        audit_request(db, request, "video_purge", v.id)
        _purge(db, settings, v)
    db.commit()
    return {"deleted": len(videos)}


class BatchBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=1000)
    action: Literal["move", "delete", "restore", "purge", "add_tags", "remove_tags"]
    folder_id: int | None = None
    tags: list[str] = []


@router.post("/videos/batch")
def batch(
    body: BatchBody,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, int]:
    if body.action == "move" and not folder_exists(db, body.folder_id):
        raise APIError(status.HTTP_400_BAD_REQUEST, "文件夹不存在", code="folder_not_found")
    videos = db.scalars(select(Video).where(Video.id.in_(body.ids))).all()
    now = utcnow()
    for v in videos:
        match body.action:
            case "move":
                v.folder_id = body.folder_id
            case "delete":
                if v.deleted_at is None:
                    audit_request(db, request, "video_trash", v.id)
                v.deleted_at = v.deleted_at or now
            case "restore":
                audit_request(db, request, "video_restore", v.id)
                v.deleted_at = None
            case "purge":
                audit_request(db, request, "video_purge", v.id)
                _purge(db, settings, v)
            case "add_tags":
                set_tags(db, v, [t.name for t in v.tags] + body.tags)
            case "remove_tags":
                remove = set(body.tags)
                set_tags(db, v, [t.name for t in v.tags if t.name not in remove])
    db.commit()
    return {"updated": len(videos)}


# ------------------------------------------------------------------ media files


@router.get("/videos/{video_id}/timing")
async def video_timing(
    video_id: str,
    keyframes_only: bool = False,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, list[float]]:
    video = get_video(db, video_id)
    if video.status != "ready":
        raise APIError(409, "请等待视频处理完成后载入帧索引", code="video_not_ready")
    try:
        return await timing_index(
            settings,
            video.id,
            abs_path(settings, video.file_path),
            int((video.meta or {}).get("video_index", 0)),
            keyframes_only=keyframes_only,
        )
    except (OSError, FFmpegError, RuntimeError, ValueError) as exc:
        raise APIError(500, f"无法读取帧索引: {exc}", code="frame_index_failed") from exc


def _cache_headers() -> dict[str, str]:
    return {"Cache-Control": "private, max-age=86400"}


@router.get("/videos/{video_id}/stream")
def stream(
    video_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> FileResponse:
    video = get_video(db, video_id, allow_deleted=True)
    path = abs_path(settings, video.playable_path or video.file_path)
    if not path.exists():
        raise APIError(status.HTTP_404_NOT_FOUND, "视频文件丢失", code="video_file_missing")
    media_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
    if path.suffix == ".mkv":
        media_type = "video/x-matroska"
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private"})


@router.get("/videos/{video_id}/download")
def download(
    video_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> FileResponse:
    video = get_video(db, video_id, allow_deleted=True)
    path = abs_path(settings, video.file_path)
    if not path.exists():
        raise APIError(status.HTTP_404_NOT_FOUND, "视频文件丢失", code="video_file_missing")
    return FileResponse(path, filename=f"{video.title}{path.suffix}")


@router.get("/videos/{video_id}/frame")
async def frame(
    video_id: str,
    t: float = Query(ge=0),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Full-resolution screenshot at time `t`."""
    video = get_video(db, video_id)
    src = abs_path(settings, video.file_path)
    out = settings.tmp_dir / f"frame-{new_id()}.jpg"
    try:
        info = await probe(settings.ffprobe, str(src))
        await derive.extract_frame(settings.ffmpeg, src, info, out, t, width=None)
        data = out.read_bytes()
    except FFmpegError as e:
        raise APIError(
            status.HTTP_500_INTERNAL_SERVER_ERROR, f"截图失败: {e}", code="frame_capture_failed"
        ) from e
    finally:
        out.unlink(missing_ok=True)
    name = f"{video.title}_{t:.2f}s.jpg"
    return Response(
        data,
        media_type="image/jpeg",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}"},
    )


class CoverTimeBody(BaseModel):
    time: float = Field(ge=0)


@router.post("/videos/{video_id}/cover")
async def set_cover_from_time(
    video_id: str,
    body: CoverTimeBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    video = get_video(db, video_id)
    src = abs_path(settings, video.file_path)
    try:
        info = await probe(settings.ffprobe, str(src))
        out = derived_dir(settings, video.id) / derive.POSTER
        await derive.extract_frame(settings.ffmpeg, src, info, out, body.time)
    except FFmpegError as e:
        raise APIError(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"设置封面失败: {e}",
            code="cover_generation_failed",
        ) from e
    video.cover_time = body.time
    video.meta = {**(video.meta or {}), "custom_cover": False}
    video.has_poster = True
    video.asset_version += 1
    db.commit()
    return video_to_dict(video)


@router.post("/videos/{video_id}/cover/upload")
async def upload_cover(
    video_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    video = get_video(db, video_id)
    tmp = settings.tmp_dir / f"cover-{new_id()}{Path(file.filename or 'x.jpg').suffix[:8]}"
    try:
        async with aiofiles.open(tmp, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                await f.write(chunk)
        out = derived_dir(settings, video.id) / derive.POSTER
        args = ["-i", str(tmp), "-frames:v", "1", "-vf",
                f"scale='min({derive.POSTER_WIDTH},iw)':-2", *derive.JPEG, str(out)]  # fmt: skip
        await run_command(ffmpeg_args(settings.ffmpeg, args, progress=False))
    except FFmpegError as e:
        raise APIError(
            status.HTTP_400_BAD_REQUEST, "无法识别的图片文件", code="cover_image_invalid"
        ) from e
    finally:
        tmp.unlink(missing_ok=True)
    video.cover_time = None
    video.meta = {**(video.meta or {}), "custom_cover": True}
    video.has_poster = True
    video.asset_version += 1
    db.commit()
    return video_to_dict(video)


@router.post("/videos/{video_id}/reprocess")
def reprocess(
    video_id: str, db: Session = Depends(get_db), jobs: JobManager = Depends(get_jobs)
) -> dict[str, Any]:
    video = get_video(db, video_id)
    video.status = "processing"
    video.asset_version += 1
    db.commit()
    return {"job_id": jobs.submit(db, "ingest", {}, [video.id]).id}


DERIVED_FILES = {
    "poster.jpg": ("image/jpeg", derive.POSTER),
    "preview.mp4": ("video/mp4", derive.PREVIEW),
    "sprite.jpg": ("image/jpeg", derive.SPRITE),
    "thumbnails.vtt": ("text/vtt", derive.VTT),
}


@router.get("/videos/{video_id}/{name}")
def derived_file(
    video_id: str, name: str, settings: Settings = Depends(get_settings)
) -> FileResponse:
    if name not in DERIVED_FILES or len(video_id) != 32 or not video_id.isalnum():
        raise APIError(status.HTTP_404_NOT_FOUND, code="derived_file_not_found")
    media_type, filename = DERIVED_FILES[name]
    path = settings.derived_dir / video_id / filename
    if not path.exists():
        raise APIError(status.HTTP_404_NOT_FOUND, "文件尚未生成", code="derived_file_pending")
    return FileResponse(path, media_type=media_type, headers=_cache_headers())
