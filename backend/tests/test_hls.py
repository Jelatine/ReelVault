from __future__ import annotations

import asyncio
import os
import subprocess

import pytest
from sqlalchemy import select

from reelvault.media.ffmpeg import ProcessHandle
from reelvault.media.hls import generate, ladder
from reelvault.media.probe import MediaInfo, probe
from reelvault.models import HlsPackage, Job, Video

from .conftest import make_video, upload_ready, wait_job


def test_three_renditions_real_segments_keyframe_alignment_and_audio(tmp_path):
    src = make_video(tmp_path / "hd.mp4", duration=8.8, size="1920x1080", rate=10)
    info = asyncio.run(probe("ffprobe", str(src)))
    out = tmp_path / "hls"
    renditions = asyncio.run(
        generate(
            "ffmpeg", "ffprobe", src, info, out, handle=ProcessHandle(), on_progress=lambda _: None
        )
    )
    assert [r["height"] for r in renditions] == [360, 720, 1080]
    master = (out / "master.m3u8").read_text()
    assert master.count("#EXT-X-STREAM-INF") == 3
    starts = []
    for i, rendition in enumerate(renditions):
        folder = out / f"v{i}"
        playlist = (folder / "index.m3u8").read_text()
        assert "#EXT-X-ENDLIST" in playlist and "#EXT-X-INDEPENDENT-SEGMENTS" in playlist
        segments = sorted(folder.glob("*.ts"))
        assert len(segments) == 3
        assert not list(folder.glob("*.tmp"))
        times = []
        for segment in segments:
            p = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "frame=key_frame,best_effort_timestamp_time",
                    "-of",
                    "csv=p=0",
                    str(segment),
                ],
                capture_output=True,
                check=True,
            )
            first = p.stdout.decode().splitlines()[0].split(",")
            assert first[0] == "1"
            times.append(float(first[1]))
            subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(segment), "-f", "null", "-"], check=True
            )
        starts.append(times)
        actual = asyncio.run(probe("ffprobe", str(folder / "index.m3u8")))
        assert actual.width == rendition["width"] and actual.audio_codec == "aac"
        assert abs(actual.duration - info.duration) < 0.12
    assert starts[0] == starts[1] == starts[2]
    assert starts[0][1] - starts[0][0] == pytest.approx(4)


def test_silent_small_anamorphic_and_ladder_no_upscaling(tmp_path):
    src = make_video(tmp_path / "sar.mp4", duration=1, audio=False, extra=["-vf", "setsar=4/3"])
    info = asyncio.run(probe("ffprobe", str(src)))
    out = tmp_path / "silent"
    variants = asyncio.run(
        generate(
            "ffmpeg", "ffprobe", src, info, out, handle=ProcessHandle(), on_progress=lambda _: None
        )
    )
    assert [(v["width"], v["height"]) for v in variants] == [(426, 240)]
    assert not asyncio.run(probe("ffprobe", str(out / "v0/index.m3u8"))).has_audio
    assert [h for h, _ in ladder(MediaInfo(duration=1, width=1280, height=720))] == [360, 720]


def enable(client, **patch):
    response = client.put(
        "/api/system/hls", json={"enabled": True, "min_size_mb": 0, "max_cache_gb": 20, **patch}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_hls_generation_cache_serving_staleness_rebuild_cleanup_and_auth(client, settings, samples):
    video = upload_ready(client, samples["a"])
    url = f"/api/videos/{video['id']}/hls"
    assert not client.get("/api/system/hls").json()["enabled"]
    assert client.post(url, json={}).status_code == 409
    enable(client)
    job = client.post(url, json={}).json()
    assert wait_job(client, job["id"])["status"] == "succeeded"
    package = client.get(url).json()["package"]
    assert package["size"] > 0 and len(package["renditions"]) == 1
    master = package["url"]
    assert client.get(master).headers["content-type"].startswith("application/vnd.apple.mpegurl")
    base = master.removesuffix("master.m3u8")
    assert client.get(base + "v0/index.m3u8").status_code == 200
    segment = client.get(base + "v0/seg_000000.ts", headers={"Range": "bytes=0-187"})
    assert segment.status_code == 206 and len(segment.content) == 188
    assert client.get(base + "v0/no.ts").status_code == 404
    assert client.get(base + "v0/%2e%2e%2f%2e%2e%2Freelvault.db").status_code == 404
    assert client.post(url, json={}).json() == {"cached": True}
    with client.app.state.sessionmaker() as db:
        # Poster-only changes leave this source-specific cache usable.
        db.get(Video, video["id"]).asset_version += 1
        db.commit()
        source = settings.data_dir / db.get(Video, video["id"]).file_path
    assert not client.get(url).json()["stale"]
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert client.get(url).json()["stale"] and client.get(master).status_code == 409
    rebuilt = client.post(url, json={}).json()
    assert wait_job(client, rebuilt["id"])["status"] == "succeeded"
    assert client.get(master).status_code == 404
    assert not (settings.derived_dir / video["id"] / "hls" / job["id"]).exists()
    assert client.delete(url).json() == {"cleared": 1}
    assert client.get(url).json()["package"] is None
    assert not (settings.derived_dir / video["id"] / "hls").exists()
    client.cookies.clear()
    for endpoint in [url, master, "/api/system/hls"]:
        assert client.get(endpoint).status_code == 401


def test_hls_disabled_threshold_budget_dedup_and_pending_source_change(client, settings, samples):
    video = upload_ready(client, samples["a"])
    url = f"/api/videos/{video['id']}/hls"
    enable(client, min_size_mb=256)
    assert client.post(url, json={"automatic": True}).status_code == 409
    assert (
        client.put("/api/system/hls", json={"enabled": True, "max_cache_gb": 0}).status_code == 422
    )
    with client.app.state.sessionmaker() as db:
        from reelvault.api.scenes import signature

        v = db.get(Video, video["id"])
        queued = Job(
            kind="hls",
            status="paused",
            video_ids=[v.id],
            params={"signature": signature(settings, v), "estimated_bytes": 21 * 1024**3},
        )
        db.add(queued)
        db.commit()
        queued_id = queued.id
    assert client.post(url, json={}).json()["id"] == queued_id
    assert client.delete(url).status_code == 409
    assert client.delete("/api/system/hls/cache").status_code == 409
    enable(client, enabled=False)
    assert client.get(f"/api/jobs/{queued_id}").json()["status"] == "canceled"
    enable(client)
    with client.app.state.sessionmaker() as db:
        # A captured queued signature cannot be applied to a different source.
        bad = Job(kind="hls", video_ids=[video["id"]], params={"signature": ["changed", 0, 0]})
        db.add(bad)
        db.commit()
        bad_id = bad.id
    client.app.state.jobs.enqueue(bad_id)
    assert wait_job(client, bad_id)["status"] == "failed"
    assert not (settings.tmp_dir / f"job-{bad_id}").exists()
    with client.app.state.sessionmaker() as db:
        db.add(
            HlsPackage(
                video_id=video["id"],
                generation="a" * 32,
                signature=[],
                renditions=[],
                size=21 * 1024**3,
            )
        )
        db.commit()
    assert client.post(url, json={}).status_code == 409
    assert client.delete("/api/system/hls/cache").json() == {"cleared": 1}
    assert client.get("/api/system/hls").json()["cache_size"] == 0
    with client.app.state.sessionmaker() as db:
        assert not list(db.scalars(select(HlsPackage)))


def test_audio_delay_preserved_as_silence_and_variable_frame_rate(tmp_path):
    import array

    source = tmp_path / "delayed.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10:duration=2",
            "-itsoffset",
            "0.7",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.3",
            "-vf",
            "setpts='if(lt(N,10),N*0.05/TB,0.5/TB+(N-10)*0.15/TB)'",
            "-fps_mode",
            "vfr",
            "-c:v",
            "ffv1",
            "-c:a",
            "pcm_s16le",
            str(source),
        ],
        check=True,
    )
    info = asyncio.run(probe("ffprobe", str(source)))
    assert info.audio_delay == pytest.approx(0.7)
    out = tmp_path / "delay"
    asyncio.run(
        generate(
            "ffmpeg",
            "ffprobe",
            source,
            info,
            out,
            handle=ProcessHandle(),
            on_progress=lambda _: None,
        )
    )
    decoded = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(out / "v0/index.m3u8"),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "1000",
            "-f",
            "f32le",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    pcm = array.array("f", decoded.stdout)
    assert max(abs(v) for v in pcm[:600]) < 0.001
    assert max(abs(v) for v in pcm[900:1100]) > 0.05
    actual = asyncio.run(probe("ffprobe", str(out / "v0/index.m3u8")))
    assert abs(actual.duration - info.duration) < 0.2


def test_hls_settings_persist_migration_roundtrip_and_orphan_cleanup(settings, tmp_path):
    from alembic import command
    from fastapi.testclient import TestClient

    from reelvault.db import make_engine
    from reelvault.main import create_app
    from reelvault.migrate import alembic_config

    from .conftest import HEADERS, login

    with TestClient(create_app(settings), headers=HEADERS) as client:
        login(client)
        enable(client, min_size_mb=17, max_cache_gb=4)
    settings.hls_enabled = False
    settings.hls_min_size_mb = 256
    orphan = settings.derived_dir / "orphan" / "hls" / ("f" * 32)
    orphan.mkdir(parents=True)
    (orphan / "stray.ts").write_bytes(b"stray")
    with TestClient(create_app(settings), headers=HEADERS) as client:
        login(client)
        prefs = client.get("/api/system/hls").json()
        assert prefs["enabled"] and prefs["min_size_mb"] == 17 and prefs["max_cache_gb"] == 4
        assert not orphan.exists()
        with client.app.state.sessionmaker() as db:
            db.add(Video(id="preserved", title="old", file_path="old.mp4"))
            db.commit()
    engine = make_engine(settings.db_path)
    command.downgrade(alembic_config(str(engine.url)), "0014")
    command.upgrade(alembic_config(str(engine.url)), "head")
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        assert db.get(Video, "preserved") is not None
        assert not list(db.scalars(select(HlsPackage)))
    engine.dispose()


def test_concurrent_hls_requests_reserve_only_one_job(client, samples, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from reelvault.api import hls as api_hls

    video = upload_ready(client, samples["a"])
    enable(client)
    original = client.app.state.jobs.submit

    def paused(db, kind, params, ids):
        job = Job(kind=kind, params=params, video_ids=ids, status="paused")
        db.add(job)
        db.commit()
        return job

    monkeypatch.setattr(client.app.state.jobs, "submit", paused)
    url = f"/api/videos/{video['id']}/hls"
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: client.post(url, json={}).json(), range(3)))
    assert len({r["id"] for r in results}) == 1
    client.post(f"/api/jobs/{results[0]['id']}/cancel")
    monkeypatch.setattr(client.app.state.jobs, "submit", original)
    from collections import namedtuple

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(api_hls.shutil, "disk_usage", lambda _: Usage(100, 100, 0))
    assert client.post(url, json={}).status_code == 507


def test_real_hls_pause_and_cancel_remove_partial_package(client, settings, samples, tmp_path):
    import time

    video = upload_ready(client, samples["long"])
    enable(client)
    wrapper = tmp_path / "ffmpeg-realtime"
    wrapper.write_text('#!/bin/sh\nexec ffmpeg -re "$@"\n')
    wrapper.chmod(0o755)
    settings.ffmpeg = str(wrapper)
    job = client.post(f"/api/videos/{video['id']}/hls", json={}).json()
    manager = client.app.state.jobs
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        ctx = manager.running.get(job["id"])
        if ctx and ctx.handle.process is not None:
            break
        time.sleep(0.02)
    else:
        pytest.fail("HLS process did not start")
    response = client.post(f"/api/jobs/{job['id']}/pause")
    assert response.status_code == 200 and response.json()["status"] == "paused"
    time.sleep(0.2)
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "paused"
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
    assert wait_job(client, job["id"])["status"] == "canceled"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and job["id"] in manager.running:
        time.sleep(0.02)
    assert job["id"] not in manager.running
    assert not (settings.tmp_dir / f"job-{job['id']}").exists()
    assert not (settings.derived_dir / video["id"] / "hls" / job["id"]).exists()
    assert client.get(f"/api/videos/{video['id']}/hls").json()["package"] is None
