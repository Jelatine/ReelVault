"""Snapshot import files; poll stable new files without following symlinks."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .jobs.manager import JobManager
from .library import VIDEO_EXTENSIONS, abs_path, stem_of, store_file
from .models import ImportSource, RuntimeSetting, Video, new_id, utcnow

log = logging.getLogger("reelvault.import")


class Importer:
    def __init__(self, settings: Settings, sessions: sessionmaker[Session], jobs: JobManager):
        self.settings, self.sessions, self.jobs = settings, sessions, jobs
        self.enabled = False
        self.stable_seconds = 10
        self.interval = 5.0
        self.last_scan: str | None = None
        self.imported = 0
        self.error: str | None = None
        self._seen: dict[str, tuple[int, int, float]] = {}
        self._lock = threading.Lock()
        self._wake = asyncio.Event()
        self._stopped = False
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        with sessions() as db:
            saved = db.get(RuntimeSetting, "import_watch")
            if saved:
                self.enabled = bool(saved.value.get("enabled", False))
                self.stable_seconds = max(2, min(3600, int(saved.value.get("stable_seconds", 10))))

    def status(self) -> dict[str, Any]:
        root = self.settings.import_dir
        return {
            "directory": str(root) if root else None,
            "available": bool(root and root.is_dir()),
            "enabled": self.enabled,
            "stable_seconds": self.stable_seconds,
            "interval_seconds": self.interval,
            "last_scan": self.last_scan,
            "imported": self.imported,
            "error": self.error,
            "scanning": self._lock.locked(),
        }

    def configure(self, enabled: bool, stable_seconds: int) -> None:
        self.enabled, self.stable_seconds = enabled, stable_seconds
        if self._loop:
            self._loop.call_soon_threadsafe(self._wake.set)

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        # Await an in-flight copy before shutting down the job manager/database.
        self._stopped = True
        self._wake.set()
        if self._task:
            await self._task

    async def _run(self) -> None:
        while not self._stopped:
            self._wake.clear()
            if self.enabled:
                try:
                    await asyncio.to_thread(self.scan, watch=True)
                except Exception as exc:
                    self.error = str(exc)
                    log.exception("import scan failed")
            if self._stopped:
                break
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.interval)

    def scan(self, *, watch: bool = False) -> int:
        with self._lock:
            root = self.settings.import_dir
            if root is None or not root.is_dir():
                raise ValueError("未配置可用的导入目录 REELVAULT_IMPORT_DIR")
            root = root.resolve()
            if root.is_relative_to(self.settings.data_dir.resolve()):
                raise ValueError("导入目录不能位于 ReelVault 数据目录内")
            self.error = None
            imported = 0
            present: set[str] = set()
            with self.sessions() as db:
                known = {str(Path(p).resolve()) for p in db.scalars(select(ImportSource.path))}
                known.update(
                    str(Path(p).resolve())
                    for p in db.scalars(
                        select(Video.source_path).where(Video.source_path.is_not(None))
                    )
                    if p is not None
                )
                for directory, dirs, files in os.walk(root, followlinks=False):
                    dirs[:] = sorted(
                        d
                        for d in dirs
                        if not (Path(directory) / d).is_symlink()
                        and not (Path(directory) / d)
                        .resolve()
                        .is_relative_to(self.settings.data_dir.resolve())
                    )
                    for name in sorted(files):
                        if watch and (not self.enabled or self._stopped):
                            break
                        path = Path(directory) / name
                        key = str(path)
                        if (
                            key in known
                            or path.suffix.lower() not in VIDEO_EXTENSIONS
                            or path.is_symlink()
                            or not path.resolve().is_relative_to(root)
                        ):
                            continue
                        try:
                            stat = path.stat()
                            if not path.is_file() or stat.st_size <= 0:
                                continue
                            signature = (stat.st_size, stat.st_mtime_ns)
                            present.add(key)
                            previous = self._seen.get(key)
                            now = time.monotonic()
                            if previous is None or previous[:2] != signature:
                                self._seen[key] = (*signature, now)
                                if watch:
                                    continue
                            elif watch and now - previous[2] < self.stable_seconds:
                                continue
                            if stat.st_size > shutil.disk_usage(self.settings.data_dir).free:
                                raise OSError("磁盘空间不足，无法导入视频")
                            snapshot = self.settings.tmp_dir / (
                                f"import-{new_id()}{path.suffix.lower()}"
                            )
                            video = None
                            try:
                                # Later source edits cannot mutate this independent copy.
                                shutil.copy2(path, snapshot, follow_symlinks=False)
                                after = path.stat()
                                if (
                                    path.is_symlink()
                                    or (after.st_size, after.st_mtime_ns) != signature
                                ):
                                    self._seen.pop(key, None)
                                    continue
                                if snapshot.is_symlink() or snapshot.stat().st_size != stat.st_size:
                                    continue
                                video = store_file(
                                    db,
                                    self.settings,
                                    snapshot,
                                    title=stem_of(name),
                                    original_name=name,
                                    folder_id=None,
                                    source_path=key,
                                    commit=False,
                                )
                                db.add(ImportSource(path=key))
                                self.jobs.submit(db, "ingest", {}, [video.id])
                            except Exception:
                                db.rollback()
                                if video is not None:
                                    abs_path(self.settings, video.file_path).unlink(missing_ok=True)
                                raise
                            finally:
                                snapshot.unlink(missing_ok=True)
                            known.add(key)
                            self._seen.pop(key, None)
                            imported += 1
                            self.imported += 1
                        except FileNotFoundError:
                            self._seen.pop(key, None)
                        except Exception as exc:
                            self.error = f"{name}: {exc}"
                            log.exception("cannot import %s", path)
            self._seen = {k: v for k, v in self._seen.items() if k in present}
            self.last_scan = utcnow().isoformat()
            return imported
