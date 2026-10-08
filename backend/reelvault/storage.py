"""Conservative shared disk estimates; reservations are persisted with uploads/jobs."""

from __future__ import annotations

import math
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .errors import APIError
from .locations import library_root
from .media import ops
from .models import Job, Upload, Video

MIB = 1024**2
ACTIVE = ("queued", "running", "paused")


def lock_budget(db: Session) -> None:
    # SQLAlchemy starts a logical transaction on reads, but sqlite hasn't BEGIN yet.
    connection = db.connection()
    if not getattr(connection.connection.driver_connection, "in_transaction", False):
        connection.exec_driver_sql("BEGIN IMMEDIATE")


@contextmanager
def budget_transaction(db: Session) -> Iterator[None]:
    """Release reservations' writer lock before a handler returns or raises.

    Successful mutations commit explicitly. Read-only/deduplicated responses and
    failures must not keep a SQLite writer while response cleanup is scheduled.
    """
    try:
        lock_budget(db)
        yield
    finally:
        db.rollback()


def upload_bytes(size: int) -> int:
    # Codec/duration are unknown until upload completes: original plus a generous
    # allowance for a playable copy and thumbnails. This is an estimate, not a cap.
    return size * 4 + 32 * MIB


def edit_bytes(db: Session, edit: ops.EditParams, ids: list[str]) -> int:
    sources = [db.get(Video, vid) for vid in dict.fromkeys(ids)]
    if not sources or any(v is None for v in sources):
        raise APIError(404, "源视频不存在", code="source_video_not_found")
    videos = [v for v in sources if v is not None]
    first = videos[0]
    durations = [max(1.0, v.duration) for v in videos]
    duration = durations[0]
    width, height, fps = first.width or 1920, first.height or 1080, first.fps or 30
    if isinstance(edit, ops.MergeParams):
        duration = sum(durations)
        width, height, fps = edit.width or width, edit.height or height, edit.fps or fps
    elif isinstance(edit, ops.CompositeParams):
        duration = {"first": durations[0], "longest": max(durations), "shortest": min(durations)}[
            edit.duration_mode
        ]
        width, height, fps = edit.width, edit.height, edit.fps
    elif isinstance(edit, ops.TrimParams):
        duration = sum(max(0, min(s.end, durations[0]) - s.start) for s in edit.segments)
    elif isinstance(edit, ops.SpeedParams):
        duration /= edit.factor
    elif isinstance(edit, ops.CropParams):
        width, height = edit.width, edit.height
    elif isinstance(edit, ops.EffectParams):
        if edit.mode == "freeze":
            duration += edit.duration
        elif edit.mode == "slow":
            duration += max(0, min(edit.end or duration, duration) - edit.start) * (
                1 / edit.factor - 1
            )
        else:
            duration += max(0, min(edit.end or duration, duration) - edit.start)
    elif isinstance(edit, ops.AnimationParams):
        duration = max(0, min(edit.end, duration) - edit.start)
        height = max(1, math.ceil(height * edit.width / width))
        width, fps = edit.width, edit.fps
        # GIF / lossless WebP can be far larger than compressed input.
        return math.ceil(duration * width * height * fps * 4) + 32 * MIB
    elif isinstance(edit, ops.ExtractAudioParams):
        return math.ceil(duration * 320_000 / 8 * 1.2) + 8 * MIB
    elif isinstance(edit, ops.CompressParams):
        if edit.resolution:
            scale = min(1.0, edit.resolution / min(width, height))
            width, height = math.ceil(width * scale), math.ceil(height * scale)
        fps = min(fps, edit.max_fps or fps)
        if edit.target_size_mb:
            target = math.ceil(edit.target_size_mb) * MIB
            playable = (
                math.ceil(duration * (width * height * fps * 0.5 + 256_000) / 8 * 1.5)
                if edit.codec == "h265"
                else 0
            )
            return max(target * 7 // 2, target * 3 // 2 + playable) + 32 * MIB
    # Source bitrate plus a pixel-rate estimate accommodates highly compressed
    # sources that grow on re-encoding. CRF 0 gets a near-raw allowance.
    if not all(math.isfinite(value) for value in (duration, width, height, fps)):
        raise APIError(400, "编辑参数必须是有限数值", code="edit_estimate_invalid")
    crf = getattr(edit, "crf", 20)
    pixel_bits = 12 if crf == 0 else max(0.2, 0.5 * 2 ** ((20 - crf) / 6))
    bitrate = (
        max(
            max(v.bitrate or v.size * 8 / d for v, d in zip(videos, durations, strict=True)),
            width * height * fps * pixel_bits,
        )
        + 256_000
    )
    output = math.ceil(duration * bitrate / 8 * 1.25)
    # Effects use FFV1/PCM intermediates rather than the final H.264 bitrate.
    lossless_staging = 0
    if isinstance(edit, ops.EffectParams):
        lossless_staging = math.ceil(
            (durations[0] + duration) * (width * height * fps * 2 + 192_000)
        )
    # Intermediates, final output, derived playable copy; replacement keeps originals.
    return output * (4 if isinstance(edit, ops.MergeParams) else 3) + lossless_staging + 32 * MIB


def job_bytes(db: Session, kind: str, params: dict[str, Any], ids: list[str]) -> int:
    if kind == "link_import":
        return int(params["limit_bytes"]) * 5 + 32 * MIB
    if kind == "edit":
        return edit_bytes(db, TypeAdapter(ops.EditParams).validate_python(params["edit"]), ids)
    if kind == "hls":
        return int(params.get("estimated_bytes", 0))
    return int(params.get("storage_bytes", 0))


def job_requirements(
    settings: Settings, kind: str, params: dict[str, Any], estimate: int
) -> dict[str, int]:
    requirements = {"local": estimate} if estimate else {}
    if kind == "link_import":
        return link_requirements(int(params["limit_bytes"]), params["storage_id"])
    target = params.get("output", {}).get("storage_id") or "local"
    if (
        kind == "edit"
        and target != "local"
        and params["edit"]["op"] not in ("animation", "extract_audio")
    ):
        # Working files/derived media live on the primary disk; independently
        # reserve a conservative output allowance on the destination disk.
        requirements[target] = estimate
    return requirements


def upload_requirements(size: int, storage_id: str) -> dict[str, int]:
    requirements = {"local": upload_bytes(size)}
    if storage_id != "local":
        requirements[storage_id] = size
    return requirements


def link_requirements(limit: int, storage_id: str) -> dict[str, int]:
    requirements = {"local": limit * 5 + 32 * MIB}
    if storage_id != "local":
        requirements[storage_id] = limit
    return requirements


def snapshot(
    db: Session,
    settings: Settings,
    required: int = 0,
    *,
    exclude_job: str | None = None,
    exclude_upload: str | None = None,
    requirements: dict[str, int] | None = None,
) -> dict[str, Any]:
    requested = requirements if requirements is not None else {"local": required}
    roots = {
        "local": settings.data_dir,
        **{key: entry.path for key, entry in settings.storage_locations.items()},
    }
    devices: dict[str, int] = {}
    usages: dict[int, Any] = {}
    for key, root in roots.items():
        try:
            if key != "local":
                root = library_root(settings, key)
            device = root.stat().st_dev
            devices[key] = device
            usages.setdefault(device, shutil.disk_usage(root))
        except OSError:
            if key == "local":
                raise
    reserved: dict[int, int] = {}

    def reserve(plan: dict[str, int]) -> None:
        for key, amount in plan.items():
            if key in devices:
                device = devices[key]
                reserved[device] = reserved.get(device, 0) + max(0, amount)

    for upload in db.scalars(select(Upload).where(Upload.id != exclude_upload)):
        path = settings.tmp_dir / f"upload-{upload.id}.part"
        try:
            written = path.stat().st_size
        except FileNotFoundError:
            written = 0
        plan = upload_requirements(upload.size, upload.storage_id)
        plan["local"] = max(0, plan["local"] - written)
        reserve(plan)
    for job in db.scalars(select(Job).where(Job.status.in_(ACTIVE), Job.id != exclude_job)):
        if "storage_plan" in job.params:
            plan = job.params["storage_plan"]
        else:
            amount = job.params.get("storage_bytes")
            if amount is None:
                try:
                    amount = job_bytes(db, job.kind, job.params, list(job.video_ids))
                except (APIError, ValueError, KeyError):
                    amount = 0
            plan = job_requirements(settings, job.kind, job.params, int(amount))
        reserve(plan)
    requested_devices: dict[int, int] = {}
    sufficient = True
    for key, amount in requested.items():
        if key not in devices:
            if amount:
                sufficient = False
            continue
        device = devices[key]
        requested_devices[device] = requested_devices.get(device, 0) + amount
    for device, amount in requested_devices.items():
        usage = usages[device]
        if usage.free - reserved.get(device, 0) < amount + 64 * MIB:
            sufficient = False
    locations = []
    for key in roots:
        if key not in devices:
            locations.append(
                {
                    "id": key,
                    "available": False,
                    "free": None,
                    "total": None,
                    "reserved_bytes": None,
                    "available_bytes": None,
                    "warning_bytes": None,
                    "low_space": False,
                    "required_bytes": requested.get(key, 0),
                }
            )
            continue
        device = devices[key]
        usage = usages[device]
        remaining = max(0, usage.free - reserved.get(device, 0))
        threshold = max(
            settings.storage_warning_mb * MIB,
            math.ceil(usage.total * settings.storage_warning_percent / 100),
        )
        locations.append(
            {
                "id": key,
                "available": True,
                "free": usage.free,
                "total": usage.total,
                "reserved_bytes": reserved.get(device, 0),
                "available_bytes": remaining,
                "warning_bytes": threshold,
                "low_space": remaining < threshold,
                "required_bytes": requested_devices.get(device, 0),
            }
        )
    primary = locations[0]
    return {
        **primary,
        "locations": locations,
        "low_space": any(item["low_space"] for item in locations),
        "sufficient": sufficient,
        "warning_mb": settings.storage_warning_mb,
        "warning_percent": settings.storage_warning_percent,
    }


def check_budget(
    db: Session,
    settings: Settings,
    required: int,
    *,
    exclude_job: str | None = None,
    exclude_upload: str | None = None,
    requirements: dict[str, int] | None = None,
) -> None:
    for key, amount in (requirements or {}).items():
        if amount and key != "local":
            library_root(settings, key)
    state = snapshot(
        db,
        settings,
        required,
        exclude_job=exclude_job,
        exclude_upload=exclude_upload,
        requirements=requirements,
    )
    if not state["sufficient"]:
        raise APIError(
            507,
            "磁盘可用空间不足，请清理空间或取消未完成任务后重试",
            code="storage_budget_exceeded",
            params=state,
        )
