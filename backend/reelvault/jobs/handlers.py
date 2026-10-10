from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from ..library import (
    abs_path,
    apply_media_info,
    derived_dir,
    rel_path,
    stem_of,
)
from ..locations import library_root, location_of
from ..media import derive, ops
from ..media.adjust import plan_adjust
from ..media.animation import plan_animation
from ..media.assets import audio_asset, image_asset, lut_asset, subtitle_asset
from ..media.composite import plan_composite
from ..media.effects import plan_effect
from ..media.probe import MediaInfo, probe
from ..media.scenes import SceneParams, detect_scenes, source_signature
from ..media.subtitles import plan_subtitle
from ..media.timing import snap_cut, timing_index
from ..media.transfer import move_output
from ..media.watermark import plan_watermark
from ..models import Job, SceneAnalysis, Video, new_id, utcnow
from ..playback_cache import remove_copy
from ..storage import MIB, check_budget
from .ai import analyse_ai
from .duplicates import duplicates
from .links import link_import
from .manager import Handler, JobContext
from .transcription import transcribe
from .vision import index_vision

edit_params: TypeAdapter[ops.EditParams] = TypeAdapter(ops.EditParams)


async def ingest(ctx: JobContext, job: Job) -> None:
    """Probe a library video and generate poster, preview, sprite and playable copy."""
    s = ctx.settings
    encoder = s.encoder
    video_id = job.video_ids[0]
    with ctx.db() as db:
        video = db.get(Video, video_id)
        if video is None:
            raise RuntimeError("视频不存在")
        src = abs_path(s, video.file_path)
        cover_time = video.cover_time
        custom_cover = bool((video.meta or {}).get("custom_cover"))
        version = video.asset_version
        old_playable = abs_path(s, video.playable_path) if video.playable_path else None

    try:
        from ..object_library import archive_original

        src = await archive_original(ctx, video.file_path)
        ctx.set_progress(0.12 if location_of(video.file_path) == "s3" else 0, "分析视频")
        info = await probe(s.ffprobe, str(src), ctx.handle)
        with ctx.db() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            video = db.get(Video, video_id)
            assert video is not None
            apply_media_info(video, info, src.stat().st_size)
            required = 32 * MIB
            eager = (
                not info.browser_playable and src.stat().st_size <= s.playable_eager_max_mb * MIB
            )
            if eager:
                rate = (
                    max(info.bitrate, info.width * info.height * (info.fps or 30) * 0.5) + 256_000
                )
                required += int(max(0, info.duration) * rate / 8 * 1.5)
            check_budget(db, s, required, exclude_job=job.id)
            job.params = {
                **job.params,
                "storage_bytes": required,
                "storage_plan": {"local": required},
            }
            stored = db.get(Job, job.id)
            if stored:
                stored.params = {
                    **stored.params,
                    "storage_bytes": required,
                    "storage_plan": {"local": required},
                }
            db.commit()

        out = derived_dir(s, video_id)
        steps = 5 if eager else 4

        ctx.stage(1, steps, "生成封面")
        if not custom_cover:
            t = cover_time if cover_time is not None else derive.default_cover_time(info)
            await derive.extract_frame(
                s.ffmpeg, src, info, out / derive.POSTER, t, handle=ctx.handle
            )
        _update(ctx, video_id, has_poster=True)

        ctx.check_canceled()
        ctx.stage(2, steps, "生成预览片段")
        preview_encoding = await derive.make_preview(
            s.ffmpeg,
            src,
            info,
            out / derive.PREVIEW,
            ctx.handle,
            encoding=ctx.manager.encoding,
            encoder=encoder,
        )
        _set_job(ctx, job.id, params={**job.params, "preview_encoding": preview_encoding})
        _update(ctx, video_id, has_preview=True)

        cb = ctx.stage(3, steps, "生成进度条缩略图")
        await derive.make_sprite(s.ffmpeg, src, info, out, ctx.handle, cb)
        vtt = out / derive.VTT
        # Absolute URL: players resolve cue images against the page, not the VTT file.
        sprite_url = f"/api/videos/{video_id}/{derive.SPRITE}?v={version}"
        vtt.write_text(vtt.read_text().replace(derive.SPRITE, sprite_url))
        _update(ctx, video_id, has_sprite=True)

        playable: str | None = None
        if eager:
            cb = ctx.stage(4, steps, "转码为浏览器可播放格式")
            target = out / derive.PLAYABLE
            playable_encoding = await derive.make_playable(
                s.ffmpeg,
                src,
                info,
                target,
                ctx.handle,
                cb,
                encoding=ctx.manager.encoding,
                encoder=encoder,
            )
            with ctx.db() as db:
                stored = db.get(Job, job.id)
                if stored:
                    stored.params = {**stored.params, "playable_encoding": playable_encoding}
                    db.commit()
            playable = rel_path(s, target)
        with ctx.db() as db:
            current = db.get(Video, video_id)
            assert current is not None
            current.playable_path, current.status, current.error = playable, "ready", None
            current.meta = {
                k: v for k, v in (current.meta or {}).items() if k != "playable_signature"
            }
            if playable:
                current.meta = {**current.meta, "playable_signature": source_signature(src)}
            db.commit()
        if old_playable and old_playable != (out / derive.PLAYABLE if playable else None):
            remove_copy(s, video_id, old_playable)
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


async def _load_sources(
    ctx: JobContext, ids: list[str], *, allow_deleted: bool = False
) -> list[tuple[Path, MediaInfo, Video]]:
    out = []
    for vid in ids:
        with ctx.db() as db:
            video = db.get(Video, vid)
            if video is None or (video.deleted_at is not None and not allow_deleted):
                raise RuntimeError("源视频不存在或已删除")
            db.expunge(video)
        path = abs_path(ctx.settings, video.file_path)
        out.append((path, await probe(ctx.settings.ffprobe, str(path), ctx.handle), video))
    return out


async def edit(ctx: JobContext, job: Job) -> None:
    s = ctx.settings
    params = edit_params.validate_python(job.params["edit"])
    asset = None
    if isinstance(params, ops.AdjustParams):
        with ctx.db() as db:
            asset = lut_asset(db, s, params)
        if asset and job.params.get("lut_sha256", asset.sha256) != asset.sha256:
            raise RuntimeError("LUT 素材版本已变化")
    if isinstance(params, ops.WatermarkParams):
        with ctx.db() as db:
            asset = image_asset(db, s, params)
        if asset and job.params.get("image_sha256", asset.sha256) != asset.sha256:
            raise RuntimeError("图片素材版本已变化")
    if isinstance(params, ops.SubtitleParams):
        with ctx.db() as db:
            asset = subtitle_asset(db, s, params)
        if asset and job.params.get("subtitle_sha256", asset.sha256) != asset.sha256:
            raise RuntimeError("字幕素材版本已变化")
    if isinstance(params, ops.AudioParams):
        with ctx.db() as db:
            asset = audio_asset(db, s, params)
        if asset and job.params.get("audio_sha256", asset.sha256) != asset.sha256:
            raise RuntimeError("音频素材版本已变化")
    output = job.params.get("output") or {}
    replace = output.get("mode") == "replace"

    ids = (
        params.video_ids
        if isinstance(params, (ops.MergeParams, ops.CompositeParams))
        else job.video_ids[:1]
    )
    ctx.set_progress(0, "读取源视频")
    loaded = await _load_sources(ctx, ids, allow_deleted=bool(job.params.get("history_replay")))
    expected = job.params.get("source_versions")
    if expected and any(v.file_path != expected[v.id] for _, _, v in loaded):
        raise RuntimeError("源视频版本已变化，无法使用原参数重新生成")
    for source_id, digest in (job.params.get("source_covers") or {}).items():
        cover = derived_dir(s, source_id) / derive.POSTER
        if not cover.is_file() or hashlib.sha256(cover.read_bytes()).hexdigest() != digest:
            raise RuntimeError("源封面已变化，无法使用原参数重新生成")
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
                if params.mode == "fast" and len(params.segments) == 1:
                    index = await timing_index(
                        s,
                        first_video.id,
                        sources[0][0],
                        sources[0][1].video_index,
                        keyframes_only=True,
                        handle=ctx.handle,
                    )
                    segment = params.segments[0]
                    if segment.start >= sources[0][1].duration:
                        raise ops.OpError("剪辑区间超出视频时长")
                    start, end = snap_cut(
                        segment.start, segment.end, index["keyframes"], sources[0][1].duration
                    )
                    if end <= start:
                        raise ops.OpError("剪辑区间超出视频时长")
                    params.segments = [ops.Segment(start=start, end=end)]
                    _set_job(
                        ctx,
                        job.id,
                        params={
                            **job.params,
                            "requested_edit": job.params["edit"],
                            "edit": params.model_dump(),
                        },
                    )
                plan = ops.plan_trim(params, sources[0], out)
            case ops.MergeParams():
                plan = ops.plan_merge(params, sources, out, tmp)
            case ops.CompositeParams():
                plan, actual = plan_composite(params, sources, out)
                _set_job(ctx, job.id, params={**job.params, "actual_composition": actual})
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
            case ops.AudioParams():
                plan = ops.plan_audio(
                    params,
                    sources[0],
                    out,
                    (abs_path(s, asset.file_path), asset.stream_index) if asset else None,
                )
            case ops.SubtitleParams():
                plan = plan_subtitle(
                    params, sources[0], out, tmp, abs_path(s, asset.file_path) if asset else None
                )
            case ops.WatermarkParams():
                plan = plan_watermark(
                    params, sources[0], out, tmp, abs_path(s, asset.file_path) if asset else None
                )
            case ops.AdjustParams():
                plan = plan_adjust(
                    params,
                    sources[0],
                    out,
                    tmp,
                    abs_path(s, asset.file_path) if asset else None,
                    int(asset.meta["dimension"]) if asset else 3,
                )
            case ops.EffectParams():
                plan, actual = plan_effect(params, sources[0], out, tmp)
                _set_job(ctx, job.id, params={**job.params, "actual_effect": actual})
            case ops.AnimationParams():
                plan = plan_animation(params, sources[0], out, tmp)
        result = out.with_suffix("." + plan.ext)

        n = len(plan.commands)
        ctx.check_canceled()
        label = ops.OP_LABELS.get(params.op, params.op)
        ctx.set_progress(0, f"{label}（{n} 步）")

        def cb(fraction: float) -> None:
            ctx.set_progress(fraction * 0.96)

        encoding = await ctx.manager.encoding.run(
            plan.commands,
            s.encoder,
            duration=plan.duration,
            on_progress=cb,
            handle=ctx.handle,
            cwd=plan.cwd,
        )
        with ctx.db() as db:
            stored = db.get(Job, job.id)
            if stored:
                stored.params = {**stored.params, "encoding": encoding}
                db.commit()
        if encoding["fallback"]:
            ctx.log(f"硬件编码失败，已回退软件编码：{encoding['fallback']}", "warning")
        if not result.exists() or result.stat().st_size == 0:
            raise RuntimeError("ffmpeg 未生成输出文件")
        ctx.set_progress(0.97, "保存结果")
        await ctx.handle.checkpoint()

        if isinstance(params, (ops.ExtractAudioParams, ops.AnimationParams)):
            dest = s.exports_dir / f"{job.id}{result.suffix}"
            await move_output(
                result,
                dest,
                handle=ctx.handle,
                on_progress=lambda fraction: ctx.set_progress(0.97 + fraction * 0.02),
            )
            name = f"{stem_of(output.get('title') or first_video.title)}{result.suffix}"
            with ctx.db() as db:
                stored = db.get(Job, job.id)
                if stored:
                    extra: dict[str, Any] = {"name": name}
                    if isinstance(params, ops.AnimationParams):
                        extra["actual_range"] = {
                            "start": params.start,
                            "end": min(params.end, sources[0][1].duration),
                        }
                    stored.params = {**stored.params, **extra}
                    stored.result_file = rel_path(s, dest)
                    db.commit()
            return

        provenance = [
            {"id": v.id, "title": v.title, "file_path": v.file_path, "size": v.size}
            for _, _, v in loaded
        ]
        if isinstance(params, ops.EmbedCoverParams):
            provenance[0]["cover_sha256"] = hashlib.sha256(cover.read_bytes()).hexdigest()
        # Replays always create a new output, including embedded covers.
        if (
            (replace or isinstance(params, ops.EmbedCoverParams))
            and not isinstance(params, (ops.MergeParams, ops.CompositeParams))
            and not job.params.get("history_replay")
        ):
            new_id_ = await _replace_with_backup(ctx, first_video, result, params, provenance)
        else:
            new_id_ = await _store_new(
                ctx,
                first_video,
                result,
                params,
                output.get("title"),
                provenance,
                output.get("storage_id"),
            )
        _set_job(ctx, job.id, result_video_id=new_id_)
        ctx.manager.submit_from_worker("ingest", {}, [new_id_], reservation_from=job.id)
    finally:
        await asyncio.to_thread(shutil.rmtree, tmp, ignore_errors=True)


def _set_job(ctx: JobContext, job_id: str, **fields: object) -> None:
    with ctx.db() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        for k, v in fields.items():
            setattr(job, k, v)
        db.commit()


async def _store_new(
    ctx: JobContext,
    source: Video,
    result: Path,
    params: ops.EditParams,
    title: str | None,
    provenance: list[dict[str, Any]],
    storage_id: str | None = None,
) -> str:
    s = ctx.settings
    vid = new_id()
    dest = library_root(s, storage_id or s.storage_default) / f"{vid}{result.suffix}"
    await move_output(
        result,
        dest,
        handle=ctx.handle,
        on_progress=lambda fraction: ctx.set_progress(0.97 + fraction * 0.02),
    )
    label = ops.OP_LABELS.get(params.op, params.op)
    with ctx.db() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
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
            source_video_id=source.id,
            edit_params=params.model_dump(),
            edited_at=utcnow(),
            edit_sources=provenance,
        )
        if src is not None:
            video.tags = list(src.tags)
        metadata_source = src or source
        video.captured_at = metadata_source.captured_at
        video.meta = {**(metadata_source.meta or {}), "custom_cover": False}
        video.metadata_overrides = dict(metadata_source.metadata_overrides or {})
        video.custom_fields = dict(metadata_source.custom_fields or {})
        if location_of(video.file_path) == "s3":
            from ..object_library import reserve_original

            reserve_original(db, s, dest.name)
        db.add(video)
        db.commit()
    return vid


async def _replace_with_backup(
    ctx: JobContext,
    source: Video,
    result: Path,
    params: ops.EditParams,
    provenance: list[dict[str, Any]],
) -> str:
    """Swap the edited file into the existing video; the old file goes to the trash
    as a separate video so it can still be restored."""
    s = ctx.settings
    location_id = location_of(source.file_path)
    # Object keys must be server-allocated, unguessable identifiers.
    suffix = new_id() if location_id == "s3" else new_id()[:8]
    new_file = library_root(s, location_id) / f"{source.id}-{suffix}{result.suffix}"
    await move_output(
        result,
        new_file,
        handle=ctx.handle,
        on_progress=lambda fraction: ctx.set_progress(0.97 + fraction * 0.02),
    )
    with ctx.db() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        video = db.get(Video, source.id)
        if video is None:
            new_file.unlink(missing_ok=True)
            raise RuntimeError("源视频已被删除")
        if video.file_path != source.file_path:
            new_file.unlink(missing_ok=True)
            raise RuntimeError("源视频已被其他任务修改，请重新提交")
        backup = Video(
            title=f"{video.title} (编辑前)"[:255],
            description=video.description,
            original_name=video.original_name,
            file_path=video.file_path,
            folder_id=video.folder_id,
            size=video.size,
            status="processing",
            deleted_at=utcnow(),
            source_video_id=video.source_video_id,
            edit_params=video.edit_params,
            edited_at=video.edited_at,
            edit_sources=video.edit_sources,
            rating=video.rating,
            favorite=video.favorite,
            cover_time=video.cover_time,
            meta=video.meta,
            captured_at=video.captured_at,
            metadata_overrides=dict(video.metadata_overrides or {}),
            custom_fields=dict(video.custom_fields or {}),
        )
        backup.tags = list(video.tags)
        db.add(backup)
        db.flush()
        # Preserve the exact cover image for embedding/replay and custom covers.
        old_cover = derived_dir(s, video.id) / derive.POSTER
        if old_cover.is_file():
            shutil.copy2(old_cover, derived_dir(s, backup.id) / derive.POSTER)
            backup.meta = {**(backup.meta or {}), "custom_cover": True}
        video.source_video_id = backup.id
        video.edit_params = params.model_dump()
        video.edited_at = utcnow()
        video.edit_sources = [{**provenance[0], "id": backup.id}]
        old_playable = video.playable_path
        video.file_path = rel_path(s, new_file)
        if location_id == "s3":
            from ..object_library import reserve_original

            reserve_original(db, s, new_file.name)
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


async def scenes(ctx: JobContext, job: Job) -> None:
    params = SceneParams.model_validate(job.params)
    source, info, video = (await _load_sources(ctx, job.video_ids))[0]
    signature = source_signature(source)
    if video.asset_version != job.params["asset_version"] or signature != job.params["signature"]:
        raise RuntimeError("源视频已变化，请重新提交场景检测")
    temp = ctx.settings.tmp_dir / f"job-{job.id}"
    temp.mkdir(parents=True, exist_ok=True)
    try:
        cuts = await detect_scenes(
            ctx.settings.ffmpeg,
            source,
            info,
            params,
            temp,
            handle=ctx.handle,
            on_progress=ctx.stage(0, 1, "检测镜头切换"),
        )
        ctx.check_canceled()
        with ctx.db() as db:
            current = db.get(Video, video.id)
            if (
                current is None
                or current.deleted_at
                or current.asset_version != video.asset_version
                or source_signature(source) != signature
            ):
                raise RuntimeError("源视频已变化，检测结果未保存")
            analysis = db.get(SceneAnalysis, video.id)
            if analysis is None:
                analysis = SceneAnalysis(video_id=video.id)
                db.add(analysis)
            analysis.asset_version, analysis.signature = video.asset_version, signature
            analysis.threshold, analysis.min_interval = params.threshold, params.min_interval
            analysis.duration, analysis.cuts, analysis.detected_at = info.duration, cuts, utcnow()
            db.commit()
        ctx.set_progress(1, f"已检测 {len(cuts)} 个切点、{len(cuts) + 1} 个章节")
    finally:
        await asyncio.to_thread(shutil.rmtree, temp, ignore_errors=True)


async def hls(ctx: JobContext, job: Job) -> None:
    from ..api.hls import check_budget
    from ..media.hls import estimated_bytes, generate
    from ..models import HlsPackage

    settings = ctx.settings
    source, info, video = (await _load_sources(ctx, job.video_ids))[0]
    sig = source_signature(source)
    if not settings.hls_enabled or sig != job.params["signature"]:
        raise RuntimeError("HLS 已关闭或源文件已变化，请重新提交")
    with ctx.db() as db:
        check_budget(db, settings, estimated_bytes(info), job.id)
    temp = settings.tmp_dir / f"job-{job.id}"
    target = settings.derived_dir / video.id / "hls" / job.id
    temp.mkdir(parents=True, exist_ok=True)
    committed = False
    old_generation = None
    try:
        renditions = await generate(
            settings.ffmpeg,
            settings.ffprobe,
            source,
            info,
            temp / "hls",
            handle=ctx.handle,
            on_progress=ctx.stage(0, 1, "生成 HLS 清晰度"),
            encoding=ctx.manager.encoding,
            encoder=settings.encoder,
        )
        ctx.check_canceled()
        size = sum(p.stat().st_size for p in (temp / "hls").rglob("*") if p.is_file())
        with ctx.db() as db:
            current = db.get(Video, video.id)
            if (
                not settings.hls_enabled
                or current is None
                or current.deleted_at
                or current.file_path != video.file_path
                or source_signature(source) != sig
            ):
                raise RuntimeError("HLS 已关闭或源文件已变化，生成结果未保存")
            check_budget(db, settings, size, job.id)
            package = db.get(HlsPackage, video.id)
            if package is None:
                package = HlsPackage(video_id=video.id)
                db.add(package)
            else:
                old_generation = package.generation
            target.parent.mkdir(parents=True, exist_ok=True)
            (temp / "hls").rename(target)
            package.generation, package.signature = job.id, sig
            package.renditions, package.size, package.created_at = renditions, size, utcnow()
            db.commit()
            committed = True
        if (
            old_generation
            and old_generation != job.id
            and re.fullmatch(r"[a-f0-9]{32}", old_generation)
        ):
            await asyncio.to_thread(
                shutil.rmtree, target.parent / old_generation, ignore_errors=True
            )
        ctx.set_progress(1, "HLS 已就绪")
    finally:
        await asyncio.to_thread(shutil.rmtree, temp, ignore_errors=True)
        if not committed:
            await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)


async def playable(ctx: JobContext, job: Job) -> None:
    source, info, video = (await _load_sources(ctx, job.video_ids))[0]
    sig = source_signature(source)
    if sig != job.params["signature"]:
        raise RuntimeError("源视频已变化，请重新生成播放缓存")
    temp = ctx.settings.tmp_dir / f"job-{job.id}"
    temp.mkdir(parents=True, exist_ok=True)
    target = derived_dir(ctx.settings, video.id) / f"playable-{job.id}.mp4"
    committed = False
    try:
        encoding = await derive.make_playable(
            ctx.settings.ffmpeg,
            source,
            info,
            temp / derive.PLAYABLE,
            ctx.handle,
            ctx.stage(0, 1, "转码为浏览器可播放格式"),
            encoding=ctx.manager.encoding,
            encoder=ctx.settings.encoder,
        )
        ctx.check_canceled()
        with ctx.db() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            current = db.get(Video, video.id)
            if (
                current is None
                or current.deleted_at
                or current.status != "ready"
                or current.file_path != video.file_path
                or source_signature(source) != sig
            ):
                raise RuntimeError("源视频已变化，播放缓存未保存")
            size = (temp / derive.PLAYABLE).stat().st_size
            check_budget(db, ctx.settings, size, exclude_job=job.id)
            old = abs_path(ctx.settings, current.playable_path) if current.playable_path else None
            (temp / derive.PLAYABLE).rename(target)
            current.playable_path = rel_path(ctx.settings, target)
            current.meta = {**(current.meta or {}), "playable_signature": sig}
            current.asset_version += 1
            stored = db.get(Job, job.id)
            if stored:
                stored.params = {**stored.params, "playable_encoding": encoding}
            db.commit()
            committed = True
        remove_copy(ctx.settings, video.id, old)
        ctx.set_progress(1, "播放缓存已就绪")
    finally:
        await asyncio.to_thread(shutil.rmtree, temp, ignore_errors=True)
        if not committed:
            target.unlink(missing_ok=True)


async def original_cache(ctx: JobContext, job: Job) -> None:
    """The manager has already fetched and pinned the original; confirm it."""
    from ..object_library import cache_valid

    with ctx.db() as db:
        video = db.get(Video, job.video_ids[0])
        if video is None:
            raise RuntimeError("视频已被删除")
        path = video.file_path
    ref = ctx.settings.s3_objects.get(path)
    if ref is not None and not cache_valid(abs_path(ctx.settings, path), ref):
        raise RuntimeError("原视频本地副本校验失败")
    ctx.set_progress(1, "原视频已保存到本地")


HANDLERS: dict[str, Handler] = {
    "original_cache": original_cache,
    "link_import": link_import,
    "ingest": ingest,
    "scenes": scenes,
    "hls": hls,
    "playable": playable,
    "transcribe": transcribe,
    "vision_index": index_vision,
    "ai_analyze": analyse_ai,
    "edit": edit,
    "duplicates": duplicates,
}
