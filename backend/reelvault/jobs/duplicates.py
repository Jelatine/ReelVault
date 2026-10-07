"""Resumable duplicate scans using the normal pause/cancel job controls."""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, insert, select

from ..library import abs_path
from ..media.duplicates import (
    ALGORITHM,
    SourceChanged,
    VisualPrint,
    content_hash,
    file_signature,
    visual_fingerprint,
    visual_similarity,
)
from ..media.ffmpeg import FFmpegError, ProgressCallback
from ..media.probe import probe
from ..models import DuplicateMatch, Job, Video, VideoFingerprint, utcnow
from .manager import JobContext


def progress_slice(ctx: JobContext, start: float, span: float, message: str) -> ProgressCallback:
    def progress(fraction: float) -> None:
        ctx.set_progress(start + span * fraction, message)

    return progress


async def duplicates(ctx: JobContext, job: Job) -> None:
    errors: list[dict[str, str]] = []
    cached = scanned = visual_failed = 0
    total = len(job.video_ids)
    for index, video_id in enumerate(job.video_ids):
        await ctx.handle.checkpoint()
        offset, length = 0.9 * index / max(1, total), 0.9 / max(1, total)
        try:
            with ctx.db() as db:
                video = db.get(Video, video_id)
                if video is None or video.deleted_at or video.status != "ready":
                    continue
                source = abs_path(ctx.settings, video.file_path)
                version, path = video.asset_version, video.file_path
                signature = file_signature(source)
                previous = db.get(VideoFingerprint, video_id)
                if (
                    previous is not None
                    and previous.signature == signature
                    and previous.asset_version == version
                    and previous.algorithm == ALGORITHM
                    and previous.visual is not None
                ):
                    cached += 1
                    continue
            digest, signature = await content_hash(
                source,
                handle=ctx.handle,
                on_progress=progress_slice(
                    ctx, offset, length * 0.6, f"文件哈希 {index + 1}/{total}"
                ),
            )
            visual, visual_error = None, None
            try:
                info = await probe(ctx.settings.ffprobe, str(source), ctx.handle)
                visual = (
                    await visual_fingerprint(
                        ctx.settings.ffmpeg,
                        source,
                        info,
                        handle=ctx.handle,
                        on_progress=progress_slice(
                            ctx,
                            offset + length * 0.6,
                            length * 0.4,
                            f"抽帧比较 {index + 1}/{total}",
                        ),
                    )
                ).to_dict()
            except (FFmpegError, ValueError) as exc:
                # Full-file identity remains useful even if decoding fails.
                visual_error = str(exc)[:2000]
                visual_failed += 1
            await ctx.handle.checkpoint()
            with ctx.db() as db:
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
                current = db.get(Video, video_id)
                if (
                    current is None
                    or current.deleted_at
                    or current.status != "ready"
                    or current.asset_version != version
                    or current.file_path != path
                    or file_signature(source) != signature
                ):
                    raise SourceChanged("Video changed; fingerprint was not saved")
                row = db.get(VideoFingerprint, video_id)
                if row is None:
                    row = VideoFingerprint(video_id=video_id)
                    db.add(row)
                row.asset_version, row.signature, row.sha256 = version, signature, digest
                row.visual, row.visual_error, row.algorithm = visual, visual_error, ALGORITHM
                row.detected_at = utcnow()
                db.commit()
                scanned += 1
        except (OSError, SourceChanged) as exc:
            errors.append({"video_id": video_id, "error": str(exc)[:2000]})
        finally:
            ctx.set_progress(offset + length, f"已检查 {index + 1}/{total}")
    # Compare unique content in the worker, never in a results-page request.
    prints: dict[str, VisualPrint] = {}
    with ctx.db() as db:
        for row, video in db.execute(
            select(VideoFingerprint, Video)
            .join(Video, Video.id == VideoFingerprint.video_id)
            .where(Video.deleted_at.is_(None), Video.status == "ready")
        ):
            try:
                if (
                    row.algorithm == ALGORITHM
                    and row.visual is not None
                    and row.asset_version == video.asset_version
                    and row.signature == file_signature(abs_path(ctx.settings, video.file_path))
                ):
                    prints.setdefault(row.sha256, VisualPrint.from_dict(row.visual))
            except OSError:
                continue
    ordered = sorted(prints.items(), key=lambda item: item[1].duration)
    matches = []
    checked = 0
    for index, (left_hash, left) in enumerate(ordered):
        await ctx.handle.checkpoint()
        for other_index in range(index + 1, len(ordered)):
            right_hash, right = ordered[other_index]
            if right.duration - left.duration > max(0.15, left.duration * 0.02):
                break
            score = visual_similarity(left, right)
            if score is not None:
                a, b = sorted((left_hash, right_hash))
                matches.append(
                    {"left_hash": a, "right_hash": b, "score": score, "algorithm": ALGORITHM}
                )
            checked += 1
            if checked % 128 == 0:
                await asyncio.sleep(0)
                await ctx.handle.checkpoint()
        ctx.set_progress(0.9 + 0.1 * (index + 1) / max(1, len(ordered)), "比较相似视频")
    await ctx.handle.checkpoint()
    with ctx.db() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        db.execute(delete(DuplicateMatch))
        for start in range(0, len(matches), 1000):
            db.execute(insert(DuplicateMatch), matches[start : start + 1000])
        current_job = db.get(Job, job.id)
        if current_job:
            current_job.params = {
                **current_job.params,
                "summary": {
                    "total": total,
                    "scanned": scanned,
                    "cached": cached,
                    "visual_failed": visual_failed,
                    "errors": errors,
                    "similar_pairs": len(matches),
                },
            }
        db.commit()
    ctx.set_progress(
        1, f"已检查 {total} 个视频；{len(errors)} 个文件失败、{visual_failed} 个抽帧失败"
    )
