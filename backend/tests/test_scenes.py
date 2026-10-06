from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from reelvault.media.probe import probe
from reelvault.media.scenes import SceneParams, detect_scenes, scene_chapters
from reelvault.models import SceneAnalysis, Video

from .conftest import upload_ready, wait_job


def scene_video(path: Path, delay: float = 0) -> Path:
    args = ["ffmpeg", "-v", "error", "-y"]
    for color in ["black", "white", "black", "white"]:
        args += ["-f", "lavfi", "-i", f"color=c={color}:s=160x120:r=10:d=1"]
    graph = "".join(f"[{i}:v]" for i in range(4)) + "concat=n=4:v=1:a=0"
    if delay:
        graph += f",setpts=PTS+{delay}/TB"
    args += ["-filter_complex", graph, "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if delay:
        args += ["-f", "matroska"]
    subprocess.run([*args, str(path)], check=True)
    return path


@pytest.mark.parametrize(
    "threshold,interval,times", [(0.4, 0.05, [1, 2, 3]), (1, 0.05, []), (0.4, 1.5, [2])]
)
def test_real_scene_threshold_and_spacing(tmp_path, threshold, interval, times):
    path = scene_video(tmp_path / "clip.mp4")
    info = asyncio.run(probe("ffprobe", str(path)))
    cuts = asyncio.run(
        detect_scenes(
            "ffmpeg", path, info, SceneParams(threshold=threshold, min_interval=interval), tmp_path
        )
    )
    assert [c["time"] for c in cuts] == pytest.approx(times, abs=0.05)
    assert all(0 <= c["score"] <= 1 for c in cuts)
    chapters = scene_chapters(cuts, info.duration)
    assert len(chapters) == len(times) + 1
    assert chapters[0]["start"] == 0
    assert chapters[-1]["end"] == info.duration
    assert all(a["end"] == b["start"] for a, b in zip(chapters, chapters[1:], strict=False))


def test_scene_api_persistence_stale_and_trim(client, settings, tmp_path):
    video = upload_ready(client, scene_video(tmp_path / "clip.mp4"))
    url = f"/api/videos/{video['id']}/scenes"
    assert client.get(url).json()["analysis"] is None
    response = client.post(url, json={"threshold": 0.4, "min_interval": 0.1})
    assert response.status_code == 200, response.text
    assert wait_job(client, response.json()["id"])["status"] == "succeeded"
    result = client.get(url).json()
    assert not result["stale"]
    assert [c["time"] for c in result["analysis"]["cuts"]] == [1, 2, 3]
    assert len(result["analysis"]["chapters"]) == 4
    assert result["job"]["kind"] == "scenes"
    assert not (settings.tmp_dir / f"job-{response.json()['id']}").exists()
    # Clearing job records retains the analysis and automatic chapters.
    client.delete("/api/jobs")
    assert client.get(url).json()["analysis"] == result["analysis"]
    chapter = result["analysis"]["chapters"][1]
    edit = client.post(
        f"/api/videos/{video['id']}/edit",
        json={
            "edit": {
                "op": "trim",
                "segments": [{"start": chapter["start"], "end": chapter["end"]}],
                "mode": "precise",
            }
        },
    )
    job = wait_job(client, edit.json()["id"])
    assert job["status"] == "succeeded", job["error"]
    # The edit job schedules ingestion separately; probe the actual result directly.
    with client.app.state.sessionmaker() as db:
        output = db.get(Video, job["result_video_id"])
        info = asyncio.run(probe("ffprobe", str(settings.data_dir / output.file_path)))
    assert info.duration == pytest.approx(1, abs=0.15)
    with client.app.state.sessionmaker() as db:
        source = db.get(Video, video["id"])
        source.asset_version += 1
        db.commit()
    assert client.get(url).json() == {"analysis": None, "stale": True, "job": None}
    assert client.post(url, json={}).status_code == 200
    with client.app.state.sessionmaker() as db:
        analysis = db.get(SceneAnalysis, video["id"])
        assert analysis is not None
    client.delete(f"/api/videos/{video['id']}")
    assert client.get(url).status_code == 404
    assert client.post(url, json={}).status_code == 404


def test_scene_duplicate_and_queued_source_change(client, settings, tmp_path):
    video = upload_ready(client, scene_video(tmp_path / "clip.mp4"))
    manager = client.app.state.jobs
    # Reserve this source, as a live paused editor would, so workers cannot claim it.
    from reelvault.jobs.manager import JobContext

    blocker = JobContext(manager, "test-source-reservation")
    blocker.video_ids = [video["id"]]
    manager.running[blocker.job_id] = blocker
    url = f"/api/videos/{video['id']}/scenes"
    # Submit with the manager's enqueue suppressed; no running subprocess involved.
    original = manager.enqueue
    manager.enqueue = lambda _: None
    try:
        first = client.post(url, json={}).json()
        assert client.post(url, json={}).json()["id"] == first["id"]
        assert client.post(url, json={"threshold": 0.2}).status_code == 409
        with client.app.state.sessionmaker() as db:
            v = db.get(Video, video["id"])
            v.asset_version += 1
            db.commit()
    finally:
        manager.enqueue = original
        manager.running.pop(blocker.job_id)
    manager.enqueue(first["id"])
    job = wait_job(client, first["id"])
    assert job["status"] == "failed"
    assert "已变化" in job["error"]
    assert client.get(url).json()["analysis"] is None


@pytest.mark.parametrize(
    "body", [{"threshold": 0}, {"threshold": 1.1}, {"min_interval": 0}, {"min_interval": 601}]
)
def test_scene_invalid_params(client, samples, body):
    video = upload_ready(client, samples["portrait"])
    assert client.post(f"/api/videos/{video['id']}/scenes", json=body).status_code == 422


def test_scene_video_delay_and_variable_timestamps(tmp_path):
    # Audio starts at zero, the video starts at 0.6; cuts must remain on the player timeline.
    inputs = []
    for color in ["black", "white", "black"]:
        inputs += ["-f", "lavfi", "-i", f"color=c={color}:s=160x120:r=10:d=1"]
    inputs += ["-f", "lavfi", "-i", "sine=duration=3.6"]
    path = tmp_path / "delayed.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            *inputs,
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0,select=not(mod(n\\,2)),setpts=PTS+0.6/TB[v]",
            "-map",
            "[v]",
            "-map",
            "3:a",
            "-fps_mode",
            "vfr",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )
    info = asyncio.run(probe("ffprobe", str(path)))
    assert info.video_delay == pytest.approx(0.6, abs=0.05)
    cuts = asyncio.run(detect_scenes("ffmpeg", path, info, SceneParams(min_interval=0.1), tmp_path))
    assert [c["time"] for c in cuts] == pytest.approx([1.6, 2.6], abs=0.06)


def test_source_file_change_without_version_invalidates(client, settings, tmp_path):
    import os

    video = upload_ready(client, scene_video(tmp_path / "source.mp4"))
    url = f"/api/videos/{video['id']}/scenes"
    job = client.post(url, json={}).json()
    assert wait_job(client, job["id"])["status"] == "succeeded"
    with client.app.state.sessionmaker() as db:
        source = settings.data_dir / db.get(Video, video["id"]).file_path
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    result = client.get(url).json()
    assert result["analysis"] is None and result["stale"]


def test_scene_auth_and_unready(anon, client, settings):
    with client.app.state.sessionmaker() as db:
        video = Video(title="unready", file_path="library/unready.mp4", status="processing")
        db.add(video)
        db.commit()
        vid = video.id
    url = f"/api/videos/{vid}/scenes"
    assert client.get(url).status_code == 409
    assert client.post(url, json={}).status_code == 409
    client.cookies.clear()
    assert client.get(url).status_code == 401
    assert client.post(url, json={}).status_code == 401


def test_scene_migration_preserves_existing_video(tmp_path):
    from alembic import command
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from reelvault.db import make_engine
    from reelvault.migrate import alembic_config, upgrade

    engine = make_engine(tmp_path / "old.db")
    config = alembic_config(str(engine.url))
    command.upgrade(config, "0012")
    with engine.begin() as db:
        vid = db.execute(
            Video.__table__.insert().values(
                title="before scenes", file_path="library/existing.mp4", status="ready"
            )
        ).inserted_primary_key[0]
    upgrade(engine)
    with Session(engine) as db:
        assert db.get(Video, vid).title == "before scenes"
        assert db.get(SceneAnalysis, vid) is None
    command.downgrade(config, "0012")
    with engine.connect() as db:
        assert (
            db.scalar(select(Video.__table__.c.title).where(Video.__table__.c.id == vid))
            == "before scenes"
        )
    engine.dispose()
