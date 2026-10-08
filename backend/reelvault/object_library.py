"""Persisted original identities; no remote I/O while a SQLite writer is held."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .config import S3Config, Settings
from .models import ObjectNamespace, OriginalObject, RuntimeSetting
from .object_store import ObjectStore, ObjectStoreError
from .object_types import KEY, ObjectRef
from .storage import budget_transaction


def descriptor(config: S3Config) -> dict[str, Any]:
    return {"bucket": config.bucket, "prefix": config.prefix, "endpoint": config.endpoint}


def load_registry(db: Session, settings: Settings) -> None:
    identity = db.get(RuntimeSetting, "object_library_id")
    settings.s3_library_id = identity.value["id"] if identity else None
    settings.s3_namespaces = {
        namespace.id: {"library_id": namespace.library_id, **namespace.descriptor}
        for namespace in db.scalars(select(ObjectNamespace))
    }
    objects = {}
    for original in db.scalars(select(OriginalObject).where(OriginalObject.state == "ready")):
        ref = ObjectRef.model_validate(original.object_ref)
        if original.path != f"s3/{original.namespace_id}/{ref.key}" or original.key != ref.key:
            raise ValueError("Original object reference does not match its immutable path")
        objects[original.path] = ref
    settings.s3_objects = objects
    settings.s3_current = next(
        (
            key
            for key, value in settings.s3_namespaces.items()
            if settings.s3
            and {k: value[k] for k in ("bucket", "prefix", "endpoint")} == descriptor(settings.s3)
            and value["library_id"] == settings.s3_library_id
        ),
        None,
    )


async def initialize(settings: Settings, sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        with budget_transaction(db):
            if db.get(RuntimeSetting, "object_library_id") is None:
                db.add(RuntimeSetting(key="object_library_id", value={"id": uuid4().hex}))
                db.commit()
        load_registry(db, settings)
    config = settings.s3
    if config is None:
        return
    try:
        if settings.s3_current:
            # Existing bindings need no network at startup: every subsequent remote
            # client validates the marker before accessing an original. Offline
            # cached originals remain usable and the website starts independently.
            return
        library_id = settings.s3_library_id
        assert library_id is not None

        def bind() -> str:
            with ObjectStore(config) as store:
                return store.bind_namespace(library_id)

        namespace_id = await asyncio.to_thread(bind)
        with sessions() as db:
            with budget_transaction(db):
                existing = db.get(ObjectNamespace, namespace_id)
                if existing and (
                    existing.library_id != library_id or existing.descriptor != descriptor(config)
                ):
                    raise ObjectStoreError("S3 namespace is already bound to another origin")
                if existing is None:
                    db.add(
                        ObjectNamespace(
                            id=namespace_id,
                            library_id=library_id,
                            descriptor=descriptor(config),
                        )
                    )
                db.commit()
            load_registry(db, settings)
        settings.s3_error = None
    except ObjectStoreError as error:
        settings.s3_error = str(error)


def bound_config(settings: Settings, namespace_id: str) -> S3Config:
    namespace = settings.s3_namespaces.get(namespace_id)
    if (
        settings.s3 is None
        or namespace is None
        or {key: namespace[key] for key in ("bucket", "prefix", "endpoint")}
        != descriptor(settings.s3)
    ):
        raise ObjectStoreError("Original object storage is not configured for this namespace")
    return settings.s3.model_copy(
        update={
            "namespace_id": namespace_id,
            "library_id": namespace["library_id"],
        }
    )


def reserve_original(db: Session, settings: Settings, key: str) -> str:
    namespace_id = settings.s3_current
    if namespace_id is None or KEY.fullmatch(key) is None:
        raise ObjectStoreError("S3 original storage is not bound to this library")
    bound_config(settings, namespace_id)
    path = f"s3/{namespace_id}/{key}"
    existing = db.get(OriginalObject, path)
    if existing is None:
        db.add(OriginalObject(path=path, namespace_id=namespace_id, key=key, state="pending"))
        db.flush()
    elif existing.state != "pending":
        raise ObjectStoreError("Original object path is already published")
    return path


def adopt_original(db: Session, settings: Settings, path: str, ref: ObjectRef) -> None:
    original = db.get(OriginalObject, path)
    if original is None or original.key != ref.key:
        raise ObjectStoreError("Original reference was not reserved")
    if ref.transfer_id != original.transfer_id:
        raise ObjectStoreError("Original upload ownership does not match")
    if original.state == "ready" and ObjectRef.model_validate(original.object_ref) != ref:
        raise ObjectStoreError("Original reference cannot be replaced")
    original.object_ref = ref.model_dump(mode="json")
    original.state = "ready"
    db.commit()
    settings.s3_objects[path] = ref


async def archive_original(ctx: Any, path: str) -> Any:
    """Publish a reserved original outside the database, then adopt its exact version."""
    from .library import abs_path
    from .locations import location_of
    from .media.object_transfer import download_original, upload_original
    from .models import Video

    if location_of(path) != "s3":
        return abs_path(ctx.settings, path)
    with ctx.db() as db:
        original = db.get(OriginalObject, path)
        if original is None:
            raise ObjectStoreError("Original object reference is missing")
        if original.state == "ready":
            return await ensure_original(ctx, path)
        namespace_id, key, token = original.namespace_id, original.key, original.transfer_id
    config = bound_config(ctx.settings, namespace_id)
    local = abs_path(ctx.settings, path)
    await ctx.handle.checkpoint()
    ctx.set_progress(0, "保存原视频到对象存储")

    def existing() -> ObjectRef | None:
        with ObjectStore(config) as store:
            try:
                return store.head(key)
            except ObjectStoreError as error:
                if error.code in {"404", "NoSuchKey", "NotFound"}:
                    return None
                raise

    ref = await asyncio.to_thread(existing)
    if ref is not None:
        if ref.transfer_id != token:
            raise ObjectStoreError("Original object is owned by another transfer")
        # A crash can occur after remote publication but before database adoption.
        # Recover canonical bytes rather than trusting a potentially changed cache.
        await download_original(
            config,
            ref,
            local,
            handle=ctx.handle,
            on_progress=lambda value: ctx.set_progress(value * 0.12),
        )
    else:
        ref = await upload_original(
            config,
            local,
            key,
            temporary_root=ctx.settings.tmp_dir,
            handle=ctx.handle,
            transfer_id=token,
            on_progress=lambda value: ctx.set_progress(value * 0.12),
        )
    await ctx.handle.checkpoint()
    with ctx.db() as db:
        removed = db.scalar(select(Video.id).where(Video.file_path == path).limit(1)) is None
    if removed:
        # Purged while transferring: the published object belongs to no record.
        def discard() -> None:
            with ObjectStore(config) as store, contextlib.suppress(ObjectStoreError):
                store.delete(ref)

        await asyncio.to_thread(discard)
        raise ObjectStoreError("Original video was removed before adoption")
    with ctx.db() as db:
        with budget_transaction(db):
            if db.scalar(select(Video.id).where(Video.file_path == path).limit(1)) is None:
                raise ObjectStoreError("Original video was removed before adoption")
        adopt_original(db, ctx.settings, path, ref)
    mark_cache(local, ref)
    return abs_path(ctx.settings, path)


async def ensure_original(ctx: Any, path: str) -> Any:
    """Get canonical original bytes for a job with an explicit staging reservation."""
    from .library import abs_path
    from .media.object_transfer import download_original
    from .models import Job
    from .storage import check_budget

    local = abs_path(ctx.settings, path)
    ref = ctx.settings.s3_objects.get(path)
    if ref is None or cache_valid(local, ref):
        return local
    namespace_id = path.split("/")[1]
    config = bound_config(ctx.settings, namespace_id)
    local.parent.mkdir(parents=True, exist_ok=True)
    with ctx.db() as db, budget_transaction(db):
        job = db.get(Job, ctx.job_id)
        if job is None:
            raise ObjectStoreError("Original download task no longer exists")
        old_plan = dict(
            job.params.get("storage_plan") or {"local": int(job.params.get("storage_bytes", 0))}
        )
        plan = {**old_plan, "local": old_plan.get("local", 0) + ref.size}
        check_budget(db, ctx.settings, plan["local"], exclude_job=job.id, requirements=plan)
        job.params = {
            **job.params,
            "storage_plan": plan,
            "storage_bytes": int(job.params.get("storage_bytes", 0)) + ref.size,
        }
        db.commit()
    try:
        ctx.set_progress(0, "下载对象存储原视频")
        await download_original(
            config,
            ref,
            local,
            handle=ctx.handle,
            on_progress=lambda value: ctx.set_progress(value * 0.12),
        )
        mark_cache(local, ref)
    finally:
        with ctx.db() as db, budget_transaction(db):
            job = db.get(Job, ctx.job_id)
            if job:
                job.params = {
                    **job.params,
                    "storage_plan": old_plan,
                    "storage_bytes": max(0, int(job.params.get("storage_bytes", 0)) - ref.size),
                }
                db.commit()
    return abs_path(ctx.settings, path)


def _cache_stat(path: Any) -> list[int]:
    value = path.stat()
    return [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns]


def cache_valid(path: Any, ref: ObjectRef) -> bool:
    import json

    marker = path.parent / (path.name + ".verified.json")
    try:
        if not path.is_file() or marker.is_symlink() or marker.stat().st_size > 1024:
            return False
        value = json.loads(marker.read_text())
        return (
            value == {"sha256": ref.sha256, "stat": _cache_stat(path)}
            and path.stat().st_size == ref.size
        )
    except (OSError, ValueError):
        return False


def mark_cache(path: Any, ref: ObjectRef) -> None:
    import os

    from .media.object_worker import write_state

    if path.stat().st_size != ref.size:
        raise ObjectStoreError("Local original size does not match the published object")
    timestamp = ref.modified.timestamp()
    os.utime(path, (timestamp, timestamp))
    path.chmod(0o400)
    marker = path.parent / (path.name + ".verified.json")
    write_state(marker, {"sha256": ref.sha256, "stat": _cache_stat(path)})


MISSING = {"404", "NoSuchKey", "NotFound", "NoSuchVersion"}


def release_remote(db: Session, settings: Settings, videos: list[Any]) -> None:
    """Delete remote originals before their records are removed.

    Runs before any write in the caller's session, so no SQLite writer is held
    during network I/O. Failures (offline, unconfigured, externally replaced
    object) raise and leave every database record intact for a later retry.
    """
    from .locations import location_of
    from .models import Video

    purging = {video.id for video in videos}
    for path in dict.fromkeys(video.file_path for video in videos):
        if location_of(path) != "s3":
            continue
        shared = db.scalar(
            select(Video.id).where(Video.file_path == path, Video.id.not_in(purging)).limit(1)
        )
        original = db.get(OriginalObject, path)
        if shared is not None or original is None:
            continue
        config = bound_config(settings, original.namespace_id)
        with ObjectStore(config) as store:
            if original.state == "ready":
                ref = ObjectRef.model_validate(original.object_ref)
            else:
                # A crash may follow remote publication but precede adoption;
                # only an object carrying this record's transfer token is ours.
                try:
                    ref = store.head(original.key)
                except ObjectStoreError as error:
                    if error.code in MISSING:
                        continue
                    raise
                if ref.transfer_id != original.transfer_id:
                    continue
            try:
                store.delete(ref)
            except ObjectStoreError as error:
                if error.code not in MISSING:
                    raise


def forget_original(db: Session, settings: Settings, path: str) -> None:
    """Drop a released original's record and verified local cache."""
    from .locations import location_of
    from .models import Video

    if location_of(path) != "s3":
        return
    if db.scalar(select(Video.id).where(Video.file_path == path).limit(1)) is not None:
        return
    original = db.get(OriginalObject, path)
    if original is not None:
        db.delete(original)
    settings.s3_objects.pop(path, None)
    try:
        from .library import abs_path

        local = abs_path(settings, path)
    except (OSError, ValueError):
        return
    local.unlink(missing_ok=True)
    (local.parent / (local.name + ".verified.json")).unlink(missing_ok=True)
