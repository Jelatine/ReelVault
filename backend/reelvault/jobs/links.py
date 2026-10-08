from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ..library import abs_path, store_file
from ..link_download import download
from ..locations import library_root
from ..media.probe import probe
from ..models import Folder, Job
from ..storage import check_budget, lock_budget, upload_bytes
from .manager import JobContext


async def link_import(ctx: JobContext, job: Job) -> None:
    settings = ctx.settings
    storage_id = job.params["storage_id"]
    library_root(settings, storage_id)
    ctx.set_progress(0, "下载链接视频")
    with tempfile.TemporaryDirectory(prefix="link-", dir=settings.tmp_dir) as temporary:
        work = Path(temporary)
        source, suggested_title = await download(ctx, job.params, work)
        for leftover in work.iterdir():
            if leftover == source:
                continue
            if leftover.is_dir() and not leftover.is_symlink():
                shutil.rmtree(leftover)
            else:
                leftover.unlink()
        ctx.set_progress(0.85, "检查下载视频")
        info = await probe(settings.ffprobe, str(source), ctx.handle)
        if info.width <= 0 or info.height <= 0 or info.duration <= 0:
            raise RuntimeError("下载内容不包含可导入的视频")
        await ctx.handle.checkpoint()
        video = None
        with ctx.db() as db:
            lock_budget(db)
            folder_id = job.params.get("folder_id")
            if folder_id is not None and db.get(Folder, folder_id) is None:
                raise RuntimeError("目标文件夹已删除")
            stored = db.get(Job, job.id)
            assert stored is not None
            # Transfer the remaining reservation to media processing atomically.
            stored.params = {**stored.params, "storage_bytes": 0, "storage_plan": {}}
            db.flush()
            size = source.stat().st_size
            plan = {"local": upload_bytes(size) - size}
            # The S3 staging copy is moved within the primary data disk, as for local.
            if storage_id not in {"local", "s3"}:
                plan[storage_id] = size
            check_budget(db, settings, sum(plan.values()), exclude_job=job.id, requirements=plan)
            try:
                video = store_file(
                    db,
                    settings,
                    source,
                    title=job.params.get("title") or suggested_title,
                    original_name=source.name,
                    folder_id=folder_id,
                    commit=False,
                    source_path=job.params["url"],
                    storage_id=storage_id,
                )
                stored.video_ids = [video.id]
                stored.result_video_id = video.id
                stored.params = {**stored.params, "imported_title": video.title}
                ctx.manager.submit(
                    db, "ingest", {"storage_bytes": upload_bytes(size) - size}, [video.id]
                )
            except Exception:
                db.rollback()
                if video:
                    target = abs_path(settings, video.file_path)
                    if target.exists():
                        shutil.move(str(target), source)
                raise
        ctx.video_ids = [video.id]
        job.video_ids = [video.id]
    ctx.set_progress(1, "链接视频已导入，正在生成播放资源")
