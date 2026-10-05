from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import TypeAdapter

from ..library import (
    abs_path,
    apply_media_info,
    derived_dir,
    rel_path,
    stem_of,
)
from ..media import derive, ops
from ..media.ffmpeg import ffmpeg_args, run_command
from ..media.probe import MediaInfo, probe
from ..models import Job, Video, new_id, utcnow
from .manager import Handler, JobContext

edit_params: TypeAdapter[ops.EditParams] = TypeAdapter(ops.EditParams)


async def ingest(ctx: JobContext, job: Job) -> None:
    """Probe a library video and generate poster, preview, sprite and playable copy."""
    s = ctx.settings
    video_id = job.video_ids[0]
    with ctx.db() as db:
        video = db.get(Video, video_id)
        if video is None:
            raise RuntimeError("视频不存在")
        src = abs_path(s, video.file_path)
        cover_time = video.cover_time
        custom_cover = bool((video.meta or {}).get("custom_cover"))
        version = video.asset_version

    try:
        ctx.set_progress(0, "分析视频")
        info = await probe(s.ffprobe, str(src))
        with ctx.db() as db:
            video = db.get(Video, video_id)
            assert video is not None
            apply_media_info(video, info, src.stat().st_size)
            db.commit()

        out = derived_dir(s, video_id)
        steps = 4 if info.browser_playable else 5

        ctx.stage(1, steps, "生成封面")
        if not custom_cover:
            t = cover_time if cover_time is not None else derive.default_cover_time(info)
            await derive.extract_frame(s.ffmpeg, src, info, out / derive.POSTER, t)
        _update(ctx, video_id, has_poster=True)

        ctx.check_canceled()
        ctx.stage(2, steps, "生成预览片段")
        await derive.make_preview(s.ffmpeg, src, info, out / derive.PREVIEW, ctx.handle)
        _update(ctx, video_id, has_preview=True)

        cb = ctx.stage(3, steps, "生成进度条缩略图")
        await derive.make_sprite(s.ffmpeg, src, info, out, ctx.handle, cb)
        vtt = out / derive.VTT
        # Absolute URL: players resolve cue images against the page, not the VTT file.
        sprite_url = f"/api/videos/{video_id}/{derive.SPRITE}?v={version}"
        vtt.write_text(vtt.read_text().replace(derive.SPRITE, sprite_url))
        _update(ctx, video_id, has_sprite=True)

        playable: str | None = None
        if not info.browser_playable:
            cb = ctx.stage(4, steps, "转码为浏览器可播放格式")
            target = out / derive.PLAYABLE
            await derive.make_playable(s.ffmpeg, src, info, target, ctx.handle, cb)
            playable = rel_path(s, target)
        _update(ctx, video_id, playable_path=playable, status="ready", error=None)
    except Exception as e:
        _update(ctx, video_id, status="error", error=str(e)[:2000])
        raise


def _update(ctx: JobContext, video_id: str, **fields: object) -> None:
    with ctx.db() as db:
        video = db.get(Video, video_id)
        if video is None:
            return
        for k, v in fields.items():
            setattr(video, k, v)
        db.commit()


async def _load_sources(ctx: JobContext, ids: list[str]) -> list[tuple[Path, MediaInfo, Video]]:
    out = []
    for vid in ids:
        with ctx.db() as db:
            video = db.get(Video, vid)
            if video is None or video.deleted_at is not None:
                raise RuntimeError("源视频不存在或已删除")
            db.expunge(video)
        path = abs_path(ctx.settings, video.file_path)
        out.append((path, await probe(ctx.settings.ffprobe, str(path)), video))
    return out


async def edit(ctx: JobContext, job: Job) -> None:
    s = ctx.settings
    params = edit_params.validate_python(job.params["edit"])
    output = job.params.get("output") or {}
    replace = output.get("mode") == "replace"

    ids = params.video_ids if isinstance(params, ops.MergeParams) else job.video_ids[:1]
    ctx.set_progress(0, "读取源视频")
    loaded = await _load_sources(ctx, ids)
    sources = [(p, i) for p, i, _ in loaded]
    first_video = loaded[0][2]

    tmp = s.tmp_dir / f"job-{job.id}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        out = tmp / "output.mp4"
        match params:
            case ops.RotateParams():
                plan = ops.plan_rotate(params, sources[0], out)
            case ops.TrimParams():
                plan = ops.plan_trim(params, sources[0], out)
            case ops.MergeParams():
                plan = ops.plan_merge(params, sources, out, tmp)
            case ops.CompressParams():
                plan = ops.plan_compress(params, sources[0], out, tmp)
            case ops.CropParams():
                plan = ops.plan_crop(params, sources[0], out)
            case ops.SpeedParams():
                plan = ops.plan_speed(params, sources[0], out)
            case ops.MuteParams():
                plan = ops.plan_mute(sources[0], out)
            case ops.ConvertParams():
                plan = ops.plan_convert(params, sources[0], out)
            case ops.ExtractAudioParams():
                plan = ops.plan_extract_audio(params, sources[0], out)
            case ops.EmbedCoverParams():
                cover = derived_dir(s, first_video.id) / derive.POSTER
                if not cover.exists():
                    raise RuntimeError("该视频还没有封面")
                plan = ops.plan_embed_cover(sources[0], cover, out)
        result = out.with_suffix("." + plan.ext)

        n = len(plan.commands)
        for i, cmd in enumerate(plan.commands):
            ctx.check_canceled()
            label = ops.OP_LABELS.get(params.op, params.op)
            message = f"{label} ({i + 1}/{n})" if n > 1 else label
            cb = ctx.stage(i, n, message)
            await run_command(
                ffmpeg_args(s.ffmpeg, cmd),
                duration=plan.duration,
                on_progress=cb,
                handle=ctx.handle,
            )
        if not result.exists() or result.stat().st_size == 0:
            raise RuntimeError("ffmpeg 未生成输出文件")
        ctx.set_progress(0.99, "保存结果")

        if isinstance(params, ops.ExtractAudioParams):
            dest = s.exports_dir / f"{job.id}{result.suffix}"
            shutil.move(str(result), dest)
            name = f"{stem_of(first_video.title)}{result.suffix}"
            _set_job(
                ctx, job.id, result_file=rel_path(s, dest), params={**job.params, "name": name}
            )
            return

        if isinstance(params, ops.EmbedCoverParams):
            _replace_in_place(ctx, first_video, result)
            return

        if replace and not isinstance(params, ops.MergeParams):
            new_id_ = _replace_with_backup(ctx, first_video, result)
        else:
            new_id_ = _store_new(ctx, first_video, result, params, output.get("title"))
        _set_job(ctx, job.id, result_video_id=new_id_)
        ctx.manager.submit_from_worker("ingest", {}, [new_id_])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _set_job(ctx: JobContext, job_id: str, **fields: object) -> None:
    with ctx.db() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        for k, v in fields.items():
            setattr(job, k, v)
        db.commit()


def _store_new(
    ctx: JobContext, source: Video, result: Path, params: ops.EditParams, title: str | None
) -> str:
    s = ctx.settings
    vid = new_id()
    dest = s.library_dir / f"{vid}{result.suffix}"
    shutil.move(str(result), dest)
    label = ops.OP_LABELS.get(params.op, params.op)
    with ctx.db() as db:
        src = db.get(Video, source.id)
        video = Video(
            id=vid,
            title=(title or f"{source.title} ({label})")[:255],
            description=source.description,
            original_name=f"{stem_of(source.original_name or source.title)}_{params.op}"
            f"{result.suffix}",
            file_path=rel_path(s, dest),
            folder_id=source.folder_id,
            size=dest.stat().st_size,
            status="processing",
        )
        if src is not None:
            video.tags = list(src.tags)
        db.add(video)
        db.commit()
    return vid


def _replace_with_backup(ctx: JobContext, source: Video, result: Path) -> str:
    """Swap the edited file into the existing video; the old file goes to the trash
    as a separate video so it can still be restored."""
    s = ctx.settings
    new_file = s.library_dir / f"{source.id}-{new_id()[:8]}{result.suffix}"
    shutil.move(str(result), new_file)
    with ctx.db() as db:
        video = db.get(Video, source.id)
        if video is None:
            raise RuntimeError("源视频已被删除")
        backup = Video(
            title=f"{video.title} (编辑前)"[:255],
            description=video.description,
            original_name=video.original_name,
            file_path=video.file_path,
            folder_id=video.folder_id,
            size=video.size,
            status="processing",
            deleted_at=utcnow(),
        )
        db.add(backup)
        old_playable = video.playable_path
        video.file_path = rel_path(s, new_file)
        video.playable_path = None
        video.size = new_file.stat().st_size
        video.status = "processing"
        video.has_preview = video.has_sprite = False
        video.asset_version += 1
        db.commit()
        backup_id = backup.id
    if old_playable:
        abs_path(s, old_playable).unlink(missing_ok=True)
    ctx.manager.submit_from_worker("ingest", {}, [backup_id])
    return source.id


def _replace_in_place(ctx: JobContext, source: Video, result: Path) -> None:
    s = ctx.settings
    new_file = s.library_dir / f"{source.id}-{new_id()[:8]}{result.suffix}"
    shutil.move(str(result), new_file)
    with ctx.db() as db:
        video = db.get(Video, source.id)
        if video is None:
            new_file.unlink(missing_ok=True)
            raise RuntimeError("源视频已被删除")
        old = abs_path(s, video.file_path)
        video.file_path = rel_path(s, new_file)
        video.size = new_file.stat().st_size
        video.asset_version += 1
        db.commit()
    old.unlink(missing_ok=True)


HANDLERS: dict[str, Handler] = {"ingest": ingest, "edit": edit}
