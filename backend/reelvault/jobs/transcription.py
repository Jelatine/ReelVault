from __future__ import annotations

import asyncio
import hashlib
import math
import shutil
import sys

from sqlalchemy import select

from ..content_search import index_track
from ..library import rel_path
from ..media.ffmpeg import ffmpeg_args, run_command
from ..media.scenes import source_signature
from ..models import Job, MediaAsset, SubtitleTrack, Video, new_id
from ..storage import check_budget
from ..transcription import check_enabled, read_result
from .manager import JobContext


async def transcribe(ctx: JobContext, job: Job) -> None:
    from .handlers import _load_sources

    source, info, video = (await _load_sources(ctx, job.video_ids))[0]
    check_enabled(ctx.settings, video)
    sig = source_signature(source)
    if sig != job.params["signature"] or info.audio_index is None:
        raise RuntimeError("源视频已变化或没有音轨，请重新提交转写")
    if (
        not math.isfinite(info.duration)
        or not 0 < info.duration <= ctx.settings.transcription_max_hours * 3600
    ):
        raise RuntimeError("源视频超过部署设置的转写时长上限")
    with ctx.db() as db:
        check_budget(
            db,
            ctx.settings,
            math.ceil(info.duration * 32000) + 32 * 1024 * 1024,
            exclude_job=job.id,
        )
    temp = ctx.settings.tmp_dir / f"job-{job.id}"
    temp.mkdir(parents=True, exist_ok=True)
    asset_id = new_id()
    target = ctx.settings.assets_dir / f"{asset_id}.vtt"
    cache = ctx.settings.assets_dir / f"{asset_id}.webvtt"
    committed = False
    try:
        await run_command(
            ffmpeg_args(
                ctx.settings.ffmpeg,
                [
                    "-i",
                    str(source),
                    "-map",
                    f"0:{info.audio_index}",
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(temp / "audio.wav"),
                ],
            ),
            duration=info.duration,
            handle=ctx.handle,
            on_progress=ctx.stage(0, 10, "提取转写音轨"),
        )
        await ctx.handle.checkpoint()
        ctx.set_progress(0.1, "加载本地语音模型")
        args = [
            sys.executable,
            "-m",
            "reelvault.media.transcription_worker",
            "--model",
            ctx.settings.transcription_model,
            "--cache",
            str(ctx.settings.data_dir / "models" / "whisper"),
            "--threads",
            str(ctx.settings.transcription_threads),
            "--audio",
            str(temp / "audio.wav"),
            "--output",
            str(temp / "transcription.json"),
            "--language",
            job.params["language"],
        ]
        if ctx.settings.transcription_download_model:
            args.append("--download")

        def progress(frac: float) -> None:
            ctx.set_progress(
                0.1 + frac * 0.85, "识别语音内容" if ctx.message != "识别语音内容" else None
            )

        await run_command(
            args, duration=info.duration, handle=ctx.handle, on_progress=progress, cwd=temp
        )
        await ctx.handle.checkpoint()
        data, language, count = read_result(temp / "transcription.json", info.duration)
        ctx.set_progress(0.97, "保存字幕与内容索引")
        with ctx.db() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            current = db.get(Video, video.id)
            if (
                not current
                or current.deleted_at
                or current.status != "ready"
                or current.file_path != video.file_path
                or source_signature(source) != sig
            ):
                raise RuntimeError("源视频已变化，转写字幕未保存")
            check_budget(db, ctx.settings, len(data) * 2, exclude_job=job.id)
            target.write_bytes(data)
            cache.write_bytes(data)
            asset = MediaAsset(
                id=asset_id,
                kind="subtitle",
                name=f"{video.title[:200]} · Whisper.vtt",
                file_path=rel_path(ctx.settings, target),
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                meta={"transcription_model": ctx.settings.transcription_model},
            )
            db.add(asset)
            db.flush()
            for old in db.scalars(
                select(SubtitleTrack).where(
                    SubtitleTrack.video_id == video.id, SubtitleTrack.generated.is_(True)
                )
            ):
                db.delete(old)
            track = SubtitleTrack(
                video_id=video.id,
                asset_id=asset_id,
                generated=True,
                label=f"Whisper · {language}",
                language=language,
                source_signature=sig,
            )
            db.add(track)
            index_track(db, track, data)
            stored = db.get(Job, job.id)
            if stored:
                stored.params = {
                    **stored.params,
                    "track_id": track.id,
                    "segments": count,
                    "detected_language": language,
                    "model": ctx.settings.transcription_model,
                }
                stored.result_video_id = video.id
            db.commit()
            committed = True
        ctx.set_progress(1, "转写完成" if count else "转写完成，未识别到语音")
    finally:
        await asyncio.to_thread(shutil.rmtree, temp, ignore_errors=True)
        if not committed:
            target.unlink(missing_ok=True)
            cache.unlink(missing_ok=True)
