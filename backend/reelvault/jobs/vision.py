from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from pathlib import Path

from sqlalchemy import delete, insert

from ..media.ffmpeg import run_command
from ..media.scenes import source_signature
from ..models import Job, VectorFrame, Video, VideoVectorIndex, new_id, utcnow
from ..storage import check_budget
from ..vision import MAX_IMAGE_BYTES, MODEL_ID, VisionParams, check_enabled, packed, sample_times
from ..visual_search import directory
from .manager import JobContext


def estimate(duration: float, params: VisionParams, maximum: int) -> int:
    return (
        len(sample_times(duration, params.interval, min(params.max_frames, maximum)))
        * (MAX_IMAGE_BYTES + 8192)
        + 32 * 1024 * 1024
    )


def read_result(output: Path, times: list[float]) -> list[dict]:
    if output.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Vision result exceeds size limit")
    data = json.loads(output.read_text())
    if not isinstance(data, dict) or data.get("model") != MODEL_ID:
        raise ValueError("Vision result model is incompatible")
    frames = data.get("frames")
    if not isinstance(frames, list) or len(frames) != len(times):
        raise ValueError("Vision frame count mismatch")
    result = []
    for ordinal, (frame, timestamp) in enumerate(zip(frames, times, strict=True)):
        if not isinstance(frame, dict) or frame.get("time") != timestamp:
            raise ValueError("Vision timestamp mismatch")
        image = output.parent / f"{ordinal:04d}.jpg"
        if (
            image.is_symlink()
            or not image.is_file()
            or not 0 < image.stat().st_size <= MAX_IMAGE_BYTES
        ):
            raise ValueError("Vision frame image missing or invalid")
        result.append(
            {"ordinal": ordinal, "timestamp": timestamp, "embedding": packed(frame.get("vector"))}
        )
    return result


async def index_vision(ctx: JobContext, job: Job) -> None:
    from .handlers import _load_sources

    source, info, video = (await _load_sources(ctx, job.video_ids))[0]
    check_enabled(ctx.settings)
    sig = source_signature(source)
    if sig != job.params["signature"]:
        raise RuntimeError("源视频已变化，请重新生成画面索引")
    params = VisionParams.model_validate(job.params)
    start = max(0, info.video_delay)
    end = (
        min(info.duration, start + info.video_duration)
        if info.video_duration > 0
        else info.duration
    )
    times = sample_times(
        end, params.interval, min(params.max_frames, ctx.settings.vision_max_frames), start
    )
    temp = ctx.settings.tmp_dir / f"job-{job.id}"
    frames_dir = temp / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    generation = new_id()
    target = directory(ctx.settings, VideoVectorIndex(video_id=video.id, generation=generation))
    old_generation = None
    committed = False
    try:
        with ctx.db() as db:
            check_budget(
                db, ctx.settings, len(times) * (MAX_IMAGE_BYTES + 8192), exclude_job=job.id
            )
        manifest = temp / "private.json"
        with os.fdopen(
            os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
        ) as stream:
            json.dump(
                {
                    "source": str(source.resolve()),
                    "video_index": info.video_index,
                    "times": times,
                    "ffmpeg": str(Path(ctx.settings.ffmpeg).resolve())
                    if "/" in ctx.settings.ffmpeg
                    else ctx.settings.ffmpeg,
                    "url": ctx.settings.vision_url,
                    "token": ctx.settings.vision_token,
                },
                stream,
            )
        output = frames_dir / "result.json"
        ctx.set_progress(0.05, "抽取视频画面并生成向量")
        await run_command(
            [
                sys.executable,
                "-m",
                "reelvault.media.vision_worker",
                "--manifest",
                str(manifest.resolve()),
                "--output",
                str(output.resolve()),
            ],
            duration=len(times),
            handle=ctx.handle,
            on_progress=lambda frac: ctx.set_progress(0.05 + 0.9 * frac),
            cwd=temp,
        )
        await ctx.handle.checkpoint()
        frames = await asyncio.to_thread(read_result, output, times)
        await ctx.handle.checkpoint()
        output.unlink()
        ctx.set_progress(0.97, "保存画面搜索索引")
        with ctx.db() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            current = db.get(Video, video.id)
            if (
                not current
                or current.deleted_at
                or current.status != "ready"
                or (current.file_path != video.file_path or source_signature(source) != sig)
            ):
                raise RuntimeError("源视频已变化，画面索引未保存")
            record = db.get(VideoVectorIndex, video.id)
            if record is None:
                record = VideoVectorIndex(video_id=video.id)
                db.add(record)
            else:
                old_generation = record.generation
            record.signature, record.model, record.generation = sig, MODEL_ID, generation
            record.interval, record.frames, record.indexed_at = (
                params.interval,
                len(frames),
                utcnow(),
            )
            db.flush()
            db.execute(delete(VectorFrame).where(VectorFrame.video_id == video.id))
            from ..ai import prune_groups, revision

            prune_groups(db)
            revision(db, advance=True)
            db.execute(insert(VectorFrame), [{**frame, "video_id": video.id} for frame in frames])
            target.parent.mkdir(parents=True, exist_ok=True)
            frames_dir.replace(target)
            stored = db.get(Job, job.id)
            if stored:
                stored.params = {**stored.params, "frames": len(frames), "model": MODEL_ID}
                stored.result_video_id = video.id
            db.commit()
            committed = True
        ctx.set_progress(1, "画面索引完成")
    finally:
        await asyncio.to_thread(shutil.rmtree, temp, ignore_errors=True)
        if not committed:
            await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
        elif (
            old_generation
            and old_generation != generation
            and re.fullmatch(r"[a-f0-9]{32}", old_generation)
        ):
            await asyncio.to_thread(
                shutil.rmtree, target.parent / old_generation, ignore_errors=True
            )
