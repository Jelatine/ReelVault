from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from sqlalchemy import delete, insert, select

from ..ai import (
    FACE_MODEL_ID,
    AiParams,
    check_ai,
    current_analysis,
    face_embedding,
    face_values,
    revision,
)
from ..media.ffmpeg import run_command
from ..models import (
    AiAnalysis,
    FaceGroup,
    FaceObservation,
    Job,
    VectorFrame,
    Video,
    VideoVectorIndex,
    utcnow,
)
from ..storage import check_budget
from ..vision import MODEL_ID
from ..visual_search import current, directory
from .manager import JobContext


async def finish_thread(function, *args, **kwargs):
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise


def snapshot(ctx: JobContext, video_id: str, path: Path, include_faces: bool = True) -> dict:
    path.unlink(missing_ok=True)
    # The connection context only commits; close it so Windows can delete the file.
    with ctx.db() as db, closing(sqlite3.connect(path)) as target, target:
        state: dict[str, Any] = {
            "revision": revision(db) if include_faces else None,
            "sources": [],
            "groups": [],
        }
        target.executescript(
            "CREATE TABLE refs (id TEXT PRIMARY KEY, group_id TEXT, frame_key TEXT,"
            " embedding BLOB);"
            "CREATE INDEX refs_group ON refs(group_id);"
            "CREATE TABLE groups (group_id TEXT PRIMARY KEY, prototype BLOB);"
            "CREATE TABLE fixed (frame_id INTEGER, ordinal INTEGER, group_id TEXT, ignored INTEGER,"
            " PRIMARY KEY(frame_id, ordinal));"
        )
        if not include_faces:
            return state
        eligible = set()
        for video, index, analysis in db.execute(
            select(Video, VideoVectorIndex, AiAnalysis)
            .select_from(Video)
            .join(VideoVectorIndex, VideoVectorIndex.video_id == Video.id)
            .join(AiAnalysis, AiAnalysis.video_id == Video.id)
            .where(
                Video.status == "ready",
                Video.deleted_at.is_(None),
                AiAnalysis.face_model == FACE_MODEL_ID,
            )
        ):
            if current_analysis(ctx.settings, video, index, analysis):
                eligible.add(video.id)
                state["sources"].append({"id": video.id, "generation": index.generation})
        for observation, index in db.execute(
            select(FaceObservation, VideoVectorIndex)
            .join(VideoVectorIndex, VideoVectorIndex.video_id == FaceObservation.video_id)
            .order_by(FaceObservation.id)
        ).yield_per(500):
            if observation.video_id not in eligible:
                continue
            if observation.video_id == video_id and observation.manual:
                target.execute(
                    "INSERT INTO fixed VALUES (?, ?, ?, ?)",
                    (
                        observation.frame_id,
                        observation.ordinal,
                        observation.group_id,
                        int(observation.ignored),
                    ),
                )
            if observation.group_id and not observation.ignored:
                key = (
                    None
                    if observation.video_id == video_id
                    else f"{observation.video_id}:{index.generation}:{observation.frame_id}"
                )
                target.execute(
                    "INSERT INTO refs VALUES (?, ?, ?, ?)",
                    ("old:" + observation.id, observation.group_id, key, observation.embedding),
                )
                target.execute(
                    "INSERT OR IGNORE INTO groups VALUES (?, ?)",
                    (observation.group_id, observation.embedding),
                )
        state["groups"] = [row[0] for row in target.execute("SELECT group_id FROM groups")]
        return state


def sources_unchanged(db, settings, state: dict, *, disk: bool) -> bool:
    if state["revision"] is not None and revision(db) != state["revision"]:
        return False
    wanted = {row["id"]: row["generation"] for row in state["sources"]}
    found = set()
    for video, index in db.execute(select(Video, VideoVectorIndex).join(VideoVectorIndex)):
        if video.id not in wanted:
            continue
        if (
            video.deleted_at
            or video.status != "ready"
            or index.generation != wanted[video.id]
            or (disk and not current(settings, video, index))
        ):
            return False
        found.add(video.id)
    return found == set(wanted)


def valid_snapshot(ctx, state):
    with ctx.db() as db:
        return sources_unchanged(db, ctx.settings, state, disk=True)


def read_result(path: Path, params: AiParams, frames: dict[int, float], state: dict) -> dict:
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("AI result exceeds size limit")
    data = json.loads(path.read_text())
    if data.get("model") != MODEL_ID or data.get("face_model") != (
        FACE_MODEL_ID if params.faces else None
    ):
        raise ValueError("Invalid AI model identity")
    suggestions, faces, groups = data.get("suggestions"), data.get("faces"), data.get("new_groups")
    if (
        not isinstance(suggestions, list)
        or len(suggestions) > params.max_tags
        or not isinstance(faces, list)
        or len(faces) > len(frames) * 16
        or (not params.faces and faces)
        or not isinstance(groups, list)
        or len(groups) > len(faces)
    ):
        raise ValueError("Invalid AI result counts")
    if any(
        not isinstance(group, str) or not re.fullmatch(r"[a-f0-9]{32}", group) for group in groups
    ) or len(set(groups)) != len(groups):
        raise ValueError("Invalid face groups")
    valid_groups = set(state["groups"]) | set(groups)
    names = set()
    for item in suggestions:
        if (
            not isinstance(item, dict)
            or item.get("name") not in params.candidates
            or item["name"] in names
            or item.get("frame_id") not in frames
            or item.get("time") != frames[item["frame_id"]]
            or isinstance(item.get("score"), bool)
            or not isinstance(item.get("score"), (int, float))
            or not params.min_score <= item["score"] <= 1
        ):
            raise ValueError("Invalid AI tag suggestion")
        names.add(item["name"])
    keys, ids = set(), set()
    stored = []
    for face in faces:
        checked = face_values(face)
        key = (face.get("frame_id"), face.get("ordinal"))
        if (
            key[0] not in frames
            or type(key[1]) is not int
            or not 0 <= key[1] < 16
            or key in keys
            or not isinstance(face.get("id"), str)
            or not re.fullmatch(r"[a-f0-9]{32}", face["id"])
            or face["id"] in ids
            or type(face.get("manual")) is not bool
            or type(face.get("ignored")) is not bool
            or (
                face.get("group_id") not in valid_groups
                and not (face["ignored"] and face.get("group_id") is None)
            )
        ):
            raise ValueError("Invalid face assignment")
        keys.add(key)
        ids.add(face["id"])
        stored.append(
            {
                "id": face["id"],
                "frame_id": key[0],
                "ordinal": key[1],
                "box": checked["box"],
                "score": checked["score"],
                "embedding": face_embedding(checked["vector"]),
                "group_id": face["group_id"],
                "manual": face["manual"],
                "ignored": face["ignored"],
            }
        )
    return {"suggestions": suggestions, "faces": stored, "groups": groups}


async def analyse_ai(ctx: JobContext, job: Job) -> None:
    params = AiParams.model_validate(job.params)
    check_ai(ctx.settings, faces=params.faces)
    video_id = job.video_ids[0]
    with ctx.db() as db:
        video, index = db.get(Video, video_id), db.get(VideoVectorIndex, video_id)
        if (
            not video
            or not index
            or video.deleted_at
            or video.status != "ready"
            or index.generation != job.params["generation"]
            or not current(ctx.settings, video, index)
        ):
            raise RuntimeError("画面索引已变化，请重新提交 AI 分析")
        frames = [
            {
                "id": row.id,
                "ordinal": row.ordinal,
                "time": row.timestamp,
                "embedding": base64.b64encode(row.embedding).decode(),
            }
            for row in db.scalars(
                select(VectorFrame)
                .where(VectorFrame.video_id == video_id)
                .order_by(VectorFrame.ordinal)
            )
        ]
        check_budget(db, ctx.settings, job.params["storage_bytes"], exclude_job=job.id)
        images = directory(ctx.settings, index)
    if not frames:
        raise RuntimeError("请先生成画面索引")
    temp = ctx.settings.tmp_dir / f"job-{job.id}"
    temp.mkdir(mode=0o700, parents=True, exist_ok=True)
    manifest, output, snapshot_file = (
        temp / "private.json",
        temp / "result.json",
        temp / "groups.sqlite",
    )
    generation = job.params["generation"]
    command = [
        sys.executable,
        "-m",
        "reelvault.media.ai_worker",
        "--manifest",
        str(manifest.resolve()),
        "--output",
        str(output.resolve()),
    ]
    try:
        for attempt in range(3):
            await ctx.handle.checkpoint()
            state = await finish_thread(snapshot, ctx, video_id, snapshot_file, params.faces)
            payload = {
                "body": params.model_dump(),
                "video_id": video_id,
                "generation": generation,
                "frames": frames,
                "frames_dir": str(images.resolve()),
                "snapshot": str(snapshot_file.resolve()),
                "url": ctx.settings.vision_url,
                "token": ctx.settings.vision_token,
            }
            if not manifest.exists():
                descriptor = os.open(manifest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "w") as stream:
                    json.dump(payload, stream)
            else:
                await finish_thread(manifest.write_text, json.dumps(payload))
            await ctx.handle.checkpoint()
            ctx.set_progress(
                0.02 if attempt == 0 else 0.8,
                "分析场景标签与人脸" if attempt == 0 else "分组已变化，重新匹配人脸",
            )
            await run_command(
                command + (["--cluster-only"] if attempt else []),
                duration=1,
                handle=ctx.handle,
                on_progress=lambda frac: ctx.set_progress(0.02 + 0.95 * frac),
                cwd=temp,
            )
            await ctx.handle.checkpoint()
            result = await finish_thread(
                read_result, output, params, {f["id"]: f["time"] for f in frames}, state
            )
            if not await finish_thread(valid_snapshot, ctx, state):
                continue
            await ctx.handle.checkpoint()
            with ctx.db() as db:
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
                video, index = db.get(Video, video_id), db.get(VideoVectorIndex, video_id)
                if (
                    not video
                    or not index
                    or video.deleted_at
                    or video.status != "ready"
                    or index.generation != generation
                    or not current(ctx.settings, video, index)
                ):
                    raise RuntimeError("源文件或画面索引已变化，AI 结果未保存")
                if not sources_unchanged(db, ctx.settings, state, disk=False):
                    continue
                analysis = db.get(AiAnalysis, video_id)
                if analysis is None:
                    analysis = AiAnalysis(video_id=video_id)
                    db.add(analysis)
                preserve_faces = (
                    not params.faces
                    and analysis.generation == generation
                    and analysis.face_model == FACE_MODEL_ID
                )
                analysis.generation, analysis.model = generation, MODEL_ID
                if not preserve_faces:
                    analysis.face_model = FACE_MODEL_ID if params.faces else None
                analysis.suggestions, analysis.analyzed_at = result["suggestions"], utcnow()
                db.flush()
                if not preserve_faces:
                    db.execute(delete(FaceObservation).where(FaceObservation.video_id == video_id))
                for group_id in result["groups"]:
                    db.add(FaceGroup(id=group_id, name=""))
                db.flush()
                if result["faces"]:
                    db.execute(
                        insert(FaceObservation),
                        [{**face, "video_id": video_id} for face in result["faces"]],
                    )
                from ..ai import prune_groups

                prune_groups(db)
                revision(db, advance=True)
                stored = db.get(Job, job.id)
                if stored:
                    stored.result_video_id = video_id
                ctx.check_canceled()
                db.commit()
            ctx.set_progress(1, "AI 分析完成，请复核标签与人脸分组")
            return
        raise RuntimeError("人脸分组持续变化，请稍后重试；原分析结果保留")
    finally:
        await finish_thread(shutil.rmtree, temp, ignore_errors=True)
