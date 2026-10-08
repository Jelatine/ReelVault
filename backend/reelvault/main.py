from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import signal
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__, webdav
from .api import (
    ai,
    assets,
    auto_groups,
    bookmarks,
    collections,
    dashboard,
    duplicates,
    folders,
    history,
    hls,
    images,
    jobs,
    links,
    locations,
    luts,
    observability,
    playback,
    playback_cache,
    scenes,
    search,
    shares,
    smart_folders,
    subtitles,
    system,
    tags,
    transcription,
    two_factor,
    videos,
    visual_search,
)
from .api import auth as auth_api
from .auth import LoginLimiter, hash_password
from .backup import backup_before_migration, library_lock
from .config import Settings
from .content_search import backfill as backfill_subtitles
from .db import make_engine, make_sessionmaker
from .errors import install_error_handlers
from .importer import Importer
from .jobs.handlers import HANDLERS
from .jobs.manager import JobManager
from .library import abs_path
from .maintenance import maintain
from .media.encoding import detect_encoders
from .media.ffmpeg import ffmpeg_version
from .migrate import upgrade
from .models import AuditEvent, HlsPackage, RuntimeSetting, Upload, User, Video
from .observability import audit, prune_audit
from .sharing import SharePrivacyMiddleware
from .updates import Updater

log = logging.getLogger("reelvault")

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = b"x-requested-with"


class CSRFMiddleware:
    """State-changing API calls must carry X-Requested-With, which browsers never add
    to cross-site form posts. Together with SameSite cookies this blocks CSRF."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] not in SAFE_METHODS
            and scope["path"].startswith("/api/")
            and not any(k == CSRF_HEADER for k, _ in scope["headers"])
        ):
            response = JSONResponse(
                {
                    "detail": "缺少 X-Requested-With 请求头",
                    "code": "csrf_header_missing",
                    "params": {},
                },
                status_code=403,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def bootstrap_admin(app: FastAPI, settings: Settings) -> None:
    with app.state.sessionmaker() as db:
        if db.scalar(select(func.count()).select_from(User)):
            return
        if settings.admin_user and settings.admin_password:
            db.add(
                User(
                    username=settings.admin_user,
                    password_hash=hash_password(settings.admin_password),
                )
            )
            db.commit()
            log.info("created admin user %s", settings.admin_user)


def cleanup_stale_uploads(app: FastAPI, settings: Settings, max_age_days: int = 7) -> None:
    cutoff = time.time() - max_age_days * 86400
    with app.state.sessionmaker() as db:
        for up in db.scalars(select(Upload)).all():
            part = settings.tmp_dir / f"upload-{up.id}.part"
            if not part.exists() or part.stat().st_mtime < cutoff:
                part.unlink(missing_ok=True)
                db.delete(up)
        db.commit()
    for d in settings.tmp_dir.glob("job-*"):
        shutil.rmtree(d, ignore_errors=True)
    with app.state.sessionmaker() as db:
        keep = {
            settings.derived_dir / p.video_id / "hls" / p.generation
            for p in db.scalars(select(HlsPackage))
        }
    from .models import VideoVectorIndex
    from .visual_search import directory as vision_directory

    with app.state.sessionmaker() as db:
        vision_keep = set()
        for index in db.scalars(select(VideoVectorIndex)):
            with contextlib.suppress(ValueError):
                vision_keep.add(vision_directory(settings, index))
    for frames in settings.derived_dir.glob("*/vision/*"):
        if (
            frames not in vision_keep
            and len(frames.name) == 32
            and all(c in "0123456789abcdef" for c in frames.name)
        ):
            shutil.rmtree(frames, ignore_errors=True)
    for query in settings.tmp_dir.glob("vision-query-*"):
        shutil.rmtree(query, ignore_errors=True)
    for directory in settings.derived_dir.glob("*/hls/*"):
        if directory not in keep:
            shutil.rmtree(directory, ignore_errors=True)
    with app.state.sessionmaker() as db:
        copies = {
            abs_path(settings, v.playable_path)
            for v in db.scalars(select(Video))
            if v.playable_path
        }
    for f in settings.derived_dir.glob("*/playable-*.mp4"):
        if f not in copies:
            f.unlink(missing_ok=True)
    # A crash during an output transfer can leave a staging file on its target disk.
    roots = [settings.library_dir, settings.exports_dir]
    roots += [entry.path / "library" for entry in settings.storage_locations.values()]
    for root in roots:
        try:
            for staged in root.glob(".reelvault-transfer-*.part"):
                staged.unlink(missing_ok=True)
        except OSError:
            log.warning("could not clean interrupted transfers on an unavailable storage location")
    for f in settings.tmp_dir.glob("frame-*"):
        f.unlink(missing_ok=True)
    for f in settings.tmp_dir.glob("import-*"):
        if f.is_file() or f.is_symlink():
            f.unlink(missing_ok=True)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir = settings.data_dir.resolve()
        settings.ensure_dirs()
        with library_lock(settings.data_dir):
            backup_before_migration(settings)
            engine = make_engine(settings.db_path)
            upgrade(engine)
            app.state.engine = engine
            app.state.sessionmaker = make_sessionmaker(engine)
            with app.state.sessionmaker() as db:
                await backfill_subtitles(db, settings)
            with app.state.sessionmaker() as db:
                saved = db.get(RuntimeSetting, "encoding")
                if saved:
                    settings.encoder = Settings(encoder=saved.value["encoder"]).encoder
                saved_hls = db.get(RuntimeSetting, "hls")
                if saved_hls:
                    validated = Settings(**saved_hls.value)
                    for key in ("hls_enabled", "hls_min_size_mb", "hls_max_cache_gb"):
                        setattr(settings, key, getattr(validated, key))
                saved_storage = db.get(RuntimeSetting, "storage")
                if saved_storage:
                    validated = Settings(**saved_storage.value)
                    settings.storage_warning_mb = validated.storage_warning_mb
                    settings.storage_warning_percent = validated.storage_warning_percent
                saved_locations = db.get(RuntimeSetting, "storage_locations")
                if saved_locations:
                    validated = Settings(**saved_locations.value)
                    settings.storage_locations = validated.storage_locations
                    settings.storage_default = validated.storage_default
                saved_links = db.get(RuntimeSetting, "link_import")
                if saved_links:
                    validated = Settings(**saved_links.value)
                    for key in (
                        "link_import_enabled",
                        "link_import_max_mb",
                        "link_import_timeout_minutes",
                    ):
                        setattr(settings, key, getattr(validated, key))
                saved_webdav = db.get(RuntimeSetting, "webdav")
                if saved_webdav:
                    validated = Settings(**saved_webdav.value)
                    settings.webdav_enabled = validated.webdav_enabled
                    settings.webdav_token_hash = validated.webdav_token_hash
            bootstrap_admin(app, settings)
            cleanup_stale_uploads(app, settings)
            app.state.ffmpeg_version = await ffmpeg_version(settings.ffmpeg)
            manager = JobManager(settings, app.state.sessionmaker, HANDLERS)
            manager.encoding.compiled = await detect_encoders(settings.ffmpeg)
            app.state.jobs = manager
            await manager.start()
            importer = Importer(settings, app.state.sessionmaker, manager)
            app.state.importer = importer
            importer.start()
            maintenance = asyncio.create_task(maintain(settings, app.state.sessionmaker))

            def request_restart() -> None:
                # Stop uvicorn through its own flag: after a SIGTERM newer uvicorn re-raises
                # the signal, and systemd treats death-by-SIGTERM as a clean stop (no restart).
                # __main__ then exits with RESTART_EXIT_CODE so the service manager restarts us.
                app.state.restart_requested = True
                server = getattr(app.state, "server", None)
                if server is not None:
                    server.should_exit = True
                else:
                    os.kill(os.getpid(), signal.SIGTERM)

            def upgrade_audit(event: str, actor: str, peer: str, details: dict[str, Any]) -> None:
                with app.state.sessionmaker() as db:
                    audit(
                        db,
                        settings,
                        event,
                        actor=actor,
                        peer=peer,
                        target=details.get("to_version"),
                        details=details,
                    )
                    db.commit()

            updater = Updater(
                settings,
                busy=lambda: len(manager.running) + manager.pending_count(include_paused=True),
                request_restart=request_restart,
                audit_event=upgrade_audit,
            )
            app.state.updater = updater
            updater.start()
            with app.state.sessionmaker() as db:
                prune_audit(db, settings)
                applied = db.scalar(
                    select(AuditEvent)
                    .where(AuditEvent.event == "upgrade_applied")
                    .order_by(AuditEvent.id.desc())
                    .limit(1)
                )
                if applied and applied.details.get("to_version") == __version__:
                    confirmed = db.scalar(
                        select(AuditEvent.id)
                        .where(
                            AuditEvent.event == "upgrade_restarted",
                            AuditEvent.details["applied_event_id"].as_integer() == applied.id,
                        )
                        .limit(1)
                    )
                    if confirmed is None:
                        audit(
                            db,
                            settings,
                            "upgrade_restarted",
                            actor=applied.actor,
                            peer=applied.peer,
                            target=__version__,
                            details={"applied_event_id": applied.id, "to_version": __version__},
                        )
                db.commit()
            log.info("ReelVault %s ready, data dir %s", __version__, settings.data_dir)
            try:
                yield
            finally:
                await importer.stop()
                maintenance.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await maintenance
                await updater.stop()
                await manager.stop()
                engine.dispose()

    app = FastAPI(title="ReelVault", version=__version__, lifespan=lifespan)
    install_error_handlers(app)
    app.state.settings = settings
    app.state.restart_requested = False
    app.state.share_limiter = LoginLimiter(5, 15 * 60)
    app.state.totp_limiter = LoginLimiter(5, 15 * 60)
    app.state.login_limiter = LoginLimiter(
        settings.login_max_failures, settings.login_lock_minutes * 60
    )
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(SharePrivacyMiddleware)

    for r in (
        webdav.router,
        webdav.settings_router,
        auth_api.router,
        two_factor.router,
        links.router,
        observability.router,
        assets.router,
        auto_groups.router,
        bookmarks.router,
        images.router,
        luts.router,
        subtitles.router,
        transcription.router,
        visual_search.router,
        ai.router,
        collections.router,
        dashboard.router,
        duplicates.router,
        history.router,
        hls.router,
        playback_cache.router,
        playback.router,
        scenes.router,
        search.router,
        shares.router,
        smart_folders.router,
        tags.router,
        videos.router,
        folders.router,
        jobs.router,
        system.router,
        locations.router,
    ):
        app.include_router(r)

    static = settings.resolve_static_dir()
    if static is not None:
        mount_spa(app, static)
    return app


def mount_spa(app: FastAPI, root: Path) -> None:
    root = root.resolve()
    index = root / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else None
            if path in {"sw.js", "manifest.webmanifest", "offline.html"}:
                cache = "no-cache"
            return FileResponse(candidate, headers={"Cache-Control": cache} if cache else None)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
