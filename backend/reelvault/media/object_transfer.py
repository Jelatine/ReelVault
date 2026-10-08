"""Owned subprocess transfers with pause/cancel and multipart cancellation cleanup."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..config import S3Config
from ..object_store import KEY, ObjectRef, ObjectStore, ObjectStoreError
from .ffmpeg import ProcessHandle, ProgressCallback, run_command

log = logging.getLogger(__name__)


def _request(config: S3Config, value: dict[str, Any], directory: Path) -> Path:
    serialized = config.model_dump(mode="json")
    for key in ("access_key", "secret_key", "session_token"):
        secret = getattr(config, key)
        serialized[key] = secret.get_secret_value() if secret is not None else None
    path = directory / "request.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump({**value, "config": serialized}, stream)
    return path


def _cleanup_put(config: S3Config, key: str, transfer_id: str, directory: Path) -> None:
    """Reconcile a killed worker, including publication before its state-file write."""
    state_path = directory / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    with ObjectStore(config) as store:
        if state.get("upload_id"):
            store.abort(key, state["upload_id"])
        try:
            ref = store.head(key)
        except ObjectStoreError as error:
            if error.code in {"404", "NoSuchKey", "NotFound"}:
                return
            raise
        # IfNoneMatch protects an existing object, and this token protects it during
        # cancellation cleanup too. Never delete a conflicting pre-existing original.
        if ref.transfer_id == transfer_id:
            store.delete(ref)


async def upload_original(
    config: S3Config,
    source: Path,
    key: str,
    *,
    temporary_root: Path,
    handle: ProcessHandle,
    on_progress: ProgressCallback | None = None,
    transfer_id: str | None = None,
) -> ObjectRef:
    if KEY.fullmatch(key) is None:
        raise ValueError("Invalid original-video object key")
    directory = Path(tempfile.mkdtemp(prefix="s3-transfer-", dir=temporary_root))
    transfer_id = transfer_id or uuid4().hex
    if re.fullmatch(r"[a-f0-9]{32}", transfer_id) is None:
        raise ValueError("Invalid S3 transfer identifier")
    succeeded = False
    try:
        path = _request(
            config,
            {
                "operation": "put",
                "source": str(source.resolve()),
                "key": key,
                "transfer_id": transfer_id,
            },
            directory,
        )
        await run_command(
            [sys.executable, "-m", "reelvault.media.object_worker", str(path)],
            duration=1,
            handle=handle,
            on_progress=on_progress,
        )
        await handle.checkpoint()
        state = json.loads((directory / "state.json").read_text())
        ref = ObjectRef.model_validate(state["ref"])
        succeeded = True
        return ref
    finally:
        # Remove credentials before retaining a failed cleanup journal.
        (directory / "request.json").unlink(missing_ok=True)
        clean = succeeded
        if not succeeded:
            try:
                await asyncio.to_thread(_cleanup_put, config, key, transfer_id, directory)
                clean = True
            except Exception:
                # Keep only a non-secret journal for recover_transfers to retry.
                (directory / "recovery.json").write_text(
                    json.dumps(
                        {
                            "key": key,
                            "transfer_id": transfer_id,
                            "bucket": config.bucket,
                            "prefix": config.prefix,
                            "endpoint": config.endpoint,
                        }
                    )
                )
                log.warning("S3 transfer cleanup deferred: %s", directory.name)
        if clean:
            await asyncio.to_thread(shutil.rmtree, directory)


async def download_original(
    config: S3Config,
    ref: ObjectRef,
    destination: Path,
    *,
    handle: ProcessHandle,
    on_progress: ProgressCallback | None = None,
) -> None:
    directory = Path(tempfile.mkdtemp(prefix="s3-transfer-", dir=destination.parent))
    try:
        path = _request(config, {"operation": "get", "ref": ref.model_dump(mode="json")}, directory)
        await run_command(
            [sys.executable, "-m", "reelvault.media.object_worker", str(path)],
            duration=1,
            handle=handle,
            on_progress=on_progress,
        )
        await handle.checkpoint()
        (directory / "verified-original").replace(destination)
    finally:
        await asyncio.to_thread(shutil.rmtree, directory)


async def recover_transfers(config: S3Config, root: Path) -> int:
    """Retry interrupted transfers before jobs start; never cross a namespace.

    The caller must own this root exclusively (the library lock enforces that at
    startup). Completed uploads already adopted by the library must not be here.
    """
    recovered = 0
    for directory in root.glob("s3-transfer-*"):
        if directory.is_symlink() or not directory.is_dir():
            continue
        request_path = directory / "request.json"
        recovery_path = directory / "recovery.json"
        try:
            path = recovery_path if recovery_path.exists() else request_path
            if path.is_symlink() or path.stat().st_size > 65536:
                continue
            value = json.loads(path.read_text())
            if path == request_path:
                recorded = value.pop("config")
                namespace = {key: recorded.get(key) for key in ("bucket", "prefix", "endpoint")}
            else:
                namespace = {key: value.get(key) for key in ("bucket", "prefix", "endpoint")}
            if namespace != {
                "bucket": config.bucket,
                "prefix": config.prefix,
                "endpoint": config.endpoint,
            }:
                continue
            if value.get("operation") == "get":
                await asyncio.to_thread(shutil.rmtree, directory)
                recovered += 1
                continue
            key, transfer_id = value["key"], value["transfer_id"]
            if KEY.fullmatch(key) is None or re.fullmatch(r"[a-f0-9]{32}", transfer_id) is None:
                continue
            # Strip credentials even if the bucket is currently offline.
            value.update(namespace)
            recovery_path.write_text(json.dumps(value))
            request_path.unlink(missing_ok=True)
            await asyncio.to_thread(_cleanup_put, config, key, transfer_id, directory)
            await asyncio.to_thread(shutil.rmtree, directory)
            recovered += 1
        except Exception:
            log.warning("S3 transfer recovery deferred: %s", directory.name)
    return recovered
