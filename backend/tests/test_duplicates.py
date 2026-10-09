from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from dataclasses import replace

import pytest

from reelvault.media.duplicates import (
    SourceChanged,
    VisualPrint,
    content_hash,
    visual_fingerprint,
    visual_similarity,
)
from reelvault.media.ffmpeg import Canceled, ProcessHandle
from reelvault.media.probe import probe

from .conftest import upload_ready, wait_job


def test_full_hash_copy_changes_and_cancellation(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"a" * (2 * 1024 * 1024) + b"tail")
    copy = tmp_path / "copy.bin"
    shutil.copyfile(source, copy)
    digest, sig = asyncio.run(content_hash(source))
    assert asyncio.run(content_hash(copy))[0] == digest
    # Identical prefix and size do not hide a changed tail.
    with copy.open("r+b") as stream:
        stream.seek(-1, 2)
        stream.write(b"!")
    assert asyncio.run(content_hash(copy))[0] != digest
    assert sig[3] == source.stat().st_size
    handle = ProcessHandle()
    handle.cancel()
    with pytest.raises(Canceled):
        asyncio.run(content_hash(source, handle=handle))

    def change(_):
        stat = source.stat()
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    with pytest.raises(SourceChanged):
        asyncio.run(content_hash(source, on_progress=change))


def make_video(path, source="testsrc2", size="320x240", duration=4):
    separator = ":" if "=" in source else "="
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"{source}{separator}s={size}:r=12:d={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def fingerprint(path):
    async def run():
        return await visual_fingerprint("ffmpeg", path, await probe("ffprobe", str(path)))

    return asyncio.run(run())


def test_real_reencode_resize_and_unrelated(tmp_path):
    source = make_video(tmp_path / "source.mp4")
    resized = tmp_path / "resized.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-vf",
            "scale=160:120",
            "-c:v",
            "libx264",
            "-crf",
            "32",
            str(resized),
        ],
        check=True,
    )
    left, right = fingerprint(source), fingerprint(resized)
    assert asyncio.run(content_hash(source))[0] != asyncio.run(content_hash(resized))[0]
    assert visual_similarity(left, right) >= 0.9
    assert VisualPrint.from_dict(left.to_dict()) == left
    unrelated = fingerprint(make_video(tmp_path / "other.mp4", "color=c=blue"))
    assert visual_similarity(left, unrelated) is None
    assert visual_similarity(left, replace(right, duration=8)) is None
    assert visual_similarity(left, replace(right, aspect=1)) is None
    assert visual_similarity(left, replace(right, algorithm=0)) is None


def test_solid_colors_and_temporal_order(tmp_path):
    red = fingerprint(make_video(tmp_path / "red.mp4", "color=c=red"))
    blue = fingerprint(make_video(tmp_path / "blue.mp4", "color=c=blue"))
    assert visual_similarity(red, blue) is None
    ordered = tmp_path / "ordered.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x240:r=12:d=2",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:r=12:d=2",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(ordered),
        ],
        check=True,
    )
    source = fingerprint(ordered)
    assert visual_similarity(source, replace(source, frames=tuple(reversed(source.frames)))) is None


def test_long_audio_tail_does_not_seek_beyond_video(tmp_path):
    path = tmp_path / "audio-tail.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x240:r=12:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=4",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )
    info = asyncio.run(probe("ffprobe", str(path)))
    assert info.duration == pytest.approx(4, abs=0.1)
    assert info.video_duration == pytest.approx(2, abs=0.1)
    result = fingerprint(path)
    assert len(result.frames) == 8
    assert result.duration == info.duration


def test_paused_hash_resumes_and_source_replacement_rejected(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"data")

    async def paused():
        handle = ProcessHandle()
        handle.pause()
        task = asyncio.create_task(content_hash(source, handle=handle))
        await asyncio.sleep(0.01)
        assert not task.done()
        handle.resume()
        return await task

    assert asyncio.run(paused())[0] == asyncio.run(content_hash(source))[0]

    def replace_file(_):
        if os.name == "nt":
            # Windows refuses to replace a file that is open; it can still be modified.
            if source.stat().st_size == 4:
                with source.open("ab") as stream:
                    stream.write(b"more")
            return
        other = tmp_path / "replacement.bin"
        other.write_bytes(b"data")
        other.replace(source)

    with pytest.raises(SourceChanged):
        asyncio.run(content_hash(source, on_progress=replace_file))


def submit_scan(client, ids):
    with client.app.state.sessionmaker() as db:
        job = client.app.state.jobs.submit(db, "duplicates", {}, ids)
        job_id = job.id
    result = wait_job(client, job_id)
    assert result["status"] == "succeeded", result["error"]
    return result


def test_scan_persists_reuses_and_rechecks_changed_files(client, settings, tmp_path, monkeypatch):
    from reelvault.models import Video, VideoFingerprint

    source = make_video(tmp_path / "source.mp4")
    a, b = upload_ready(client, source), upload_ready(client, source)
    ids = [a["id"], b["id"]]
    first = submit_scan(client, ids)
    assert first["params"]["summary"]["scanned"] == 2
    with client.app.state.sessionmaker() as db:
        left, right = (db.get(VideoFingerprint, vid) for vid in ids)
        assert left.sha256 == right.sha256
        assert (
            visual_similarity(
                VisualPrint.from_dict(left.visual), VisualPrint.from_dict(right.visual)
            )
            == 1
        )
    # A cached scan must avoid probing or decoding unchanged originals.
    import importlib

    module = importlib.import_module("reelvault.jobs.duplicates")
    original = module.probe

    async def unexpected(*args):
        raise AssertionError("Unchanged video was decoded again")

    monkeypatch.setattr(module, "probe", unexpected)
    assert submit_scan(client, ids)["params"]["summary"]["cached"] == 2
    monkeypatch.setattr(module, "probe", original)
    with client.app.state.sessionmaker() as db:
        video = db.get(Video, ids[0])
        path = settings.data_dir / video.file_path
        old_signature = db.get(VideoFingerprint, video.id).signature
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    result = submit_scan(client, ids)
    assert result["params"]["summary"]["scanned"] == 1
    assert result["params"]["summary"]["cached"] == 1
    with client.app.state.sessionmaker() as db:
        assert db.get(VideoFingerprint, ids[0]).signature != old_signature
    client.delete("/api/jobs")
    with client.app.state.sessionmaker() as db:
        assert db.get(VideoFingerprint, ids[0]) is not None


def test_scan_exact_hash_survives_decode_failure_and_reports_missing(client, settings, tmp_path):
    from reelvault.models import Video, VideoFingerprint

    video = upload_ready(client, make_video(tmp_path / "source.mp4"))
    with client.app.state.sessionmaker() as db:
        path = settings.data_dir / db.get(Video, video["id"]).file_path
    path.write_bytes(b"no longer decodable video")
    result = submit_scan(client, [video["id"]])
    assert result["params"]["summary"]["visual_failed"] == 1
    with client.app.state.sessionmaker() as db:
        row = db.get(VideoFingerprint, video["id"])
        assert row.sha256 == asyncio.run(content_hash(path))[0]
        assert row.visual is None and row.visual_error
    path.unlink()
    result = submit_scan(client, [video["id"]])
    assert result["params"]["summary"]["errors"][0]["video_id"] == video["id"]


def test_fingerprint_migration_preserves_metadata_and_cascades(tmp_path):
    from alembic import command
    from sqlalchemy import inspect
    from sqlalchemy.orm import Session

    from reelvault.db import make_engine
    from reelvault.migrate import alembic_config, upgrade
    from reelvault.models import Video, VideoFingerprint

    engine = make_engine(tmp_path / "old.db")
    config = alembic_config(str(engine.url))
    command.upgrade(config, "0021")
    with Session(engine) as db:
        video = Video(
            title="preserved", file_path="library/original.mp4", custom_fields={"project": "sample"}
        )
        db.add(video)
        db.commit()
        vid = video.id
    upgrade(engine)
    with Session(engine) as db:
        assert db.get(Video, vid).custom_fields == {"project": "sample"}
        db.add(
            VideoFingerprint(
                video_id=vid, asset_version=1, signature=[], sha256="a" * 64, algorithm=1
            )
        )
        db.commit()
    command.downgrade(config, "0021")
    assert "video_fingerprints" not in inspect(engine).get_table_names()
    upgrade(engine)
    with Session(engine) as db:
        db.add(
            VideoFingerprint(
                video_id=vid, asset_version=1, signature=[], sha256="a" * 64, algorithm=1
            )
        )
        db.commit()
        db.delete(db.get(Video, vid))
        db.commit()
        assert db.get(VideoFingerprint, vid) is None
    engine.dispose()


def resolution(group, keep_id=None, merge=True):
    keep_id = keep_id or group["keep_id"]
    return {
        "keep_id": keep_id,
        "remove_ids": [v["id"] for v in group["videos"] if v["id"] != keep_id],
        "expected_hashes": {v["id"]: v["sha256"] for v in group["videos"]},
        "kind": group["kind"],
        "merge": merge,
    }


def test_duplicate_api_exact_similar_pagination_and_reversible_merge(client, settings, tmp_path):
    from reelvault.models import Video

    source = make_video(tmp_path / "source.mp4")
    resized = tmp_path / "smaller.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-vf",
            "scale=160:120",
            "-c:v",
            "libx264",
            "-crf",
            "32",
            str(resized),
        ],
        check=True,
    )
    a, b, c = (upload_ready(client, p) for p in [source, source, resized])
    assert client.get("/api/duplicates").json()["unscanned"] == 3
    job = client.post("/api/duplicates/scan")
    assert job.status_code == 200, job.text
    assert wait_job(client, job.json()["id"])["status"] == "succeeded"
    report = client.get("/api/duplicates").json()
    assert report["total"] == 2 and report["fingerprinted"] == 3
    exact, similar = report["items"]
    assert exact["kind"] == "exact" and len(exact["videos"]) == 2
    assert similar["kind"] == "similar" and similar["score"] >= 0.9
    assert similar["keep_id"] != c["id"]
    assert client.get("/api/duplicates", params={"kind": "exact"}).json()["total"] == 1
    assert (
        client.get("/api/duplicates", params={"page_size": 1, "page": 2}).json()["items"][0]["kind"]
        == "similar"
    )
    client.patch(f"/api/videos/{a['id']}", json={"tags": ["retained"], "rating": 2})
    client.patch(f"/api/videos/{b['id']}", json={"tags": ["merged"], "rating": 5, "favorite": True})
    client.patch(f"/api/videos/{a['id']}/metadata", json={"custom_fields": {"shared": "target"}})
    client.patch(
        f"/api/videos/{b['id']}/metadata",
        json={"custom_fields": {"shared": "source", "new": "value"}},
    )
    collection = client.post(
        "/api/collections", json={"name": "duplicate source", "video_ids": [b["id"]]}
    ).json()
    body = resolution(exact, a["id"])
    response = client.post("/api/duplicates/resolve", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["skipped_custom_fields"] == ["shared"]
    kept = response.json()["kept"]
    assert set(kept["tags"]) == {"retained", "merged"}
    assert kept["rating"] == 5 and kept["favorite"]
    assert kept["metadata"]["custom_fields"] == {"shared": "target", "new": "value"}
    assert [
        v["id"] for v in client.get(f"/api/collections/{collection['id']}").json()["items"]
    ] == [a["id"]]
    with client.app.state.sessionmaker() as db:
        original = db.get(Video, b["id"])
        assert original.deleted_at
        assert (settings.data_dir / original.file_path).read_bytes() == source.read_bytes()
        assert original.custom_fields["shared"] == "source"
    assert client.get("/api/duplicates", params={"kind": "exact"}).json()["total"] == 0
    assert client.post(f"/api/videos/{b['id']}/restore").status_code == 200
    assert client.get("/api/duplicates", params={"kind": "exact"}).json()["total"] == 1
    # Explicit visual review may retain either file; deletion-only leaves target metadata intact.
    body = resolution(similar, c["id"], merge=False)
    response = client.post("/api/duplicates/resolve", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["kept"]["rating"] == 0


def test_duplicate_stale_unrelated_busy_and_auth(client, settings, tmp_path, monkeypatch):
    from reelvault.models import Video

    source = make_video(tmp_path / "source.mp4")
    a, b = upload_ready(client, source), upload_ready(client, source)
    unrelated = upload_ready(client, make_video(tmp_path / "unrelated.mp4", "color=c=blue"))
    submit_scan(client, [a["id"], b["id"], unrelated["id"]])
    group = client.get("/api/duplicates").json()["items"][0]
    body = resolution(group)
    # Pending jobs reserve the source even when no process has started.
    manager = client.app.state.jobs
    original_enqueue = manager.enqueue
    monkeypatch.setattr(manager, "enqueue", lambda _: None)
    with client.app.state.sessionmaker() as db:
        job = manager.submit(db, "scenes", {}, [body["keep_id"]])
        pending_id = job.id
    assert (
        client.post("/api/duplicates/resolve", json=body).json()["code"] == "duplicate_videos_busy"
    )
    client.post(f"/api/jobs/{pending_id}/cancel")
    first = client.post("/api/duplicates/scan").json()
    assert client.post("/api/duplicates/scan").json()["id"] == first["id"]
    client.post(f"/api/jobs/{first['id']}/cancel")
    monkeypatch.setattr(manager, "enqueue", original_enqueue)
    with client.app.state.sessionmaker() as db:
        path = settings.data_dir / db.get(Video, body["remove_ids"][0]).file_path
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert (
        client.post("/api/duplicates/resolve", json=body).json()["code"] == "duplicate_result_stale"
    )
    assert client.get("/api/duplicates").json()["unscanned"] == 1
    submit_scan(client, [a["id"], b["id"], unrelated["id"]])
    with client.app.state.sessionmaker() as db:
        from reelvault.models import VideoFingerprint

        bad_hash = db.get(VideoFingerprint, unrelated["id"]).sha256
    invalid = {
        **body,
        "remove_ids": [unrelated["id"]],
        "kind": "similar",
        "expected_hashes": {
            body["keep_id"]: body["expected_hashes"][body["keep_id"]],
            unrelated["id"]: bad_hash,
        },
    }
    assert (
        client.post("/api/duplicates/resolve", json=invalid).json()["code"] == "duplicate_not_match"
    )
    assert (
        client.post(
            "/api/duplicates/resolve", json={**body, "remove_ids": [body["keep_id"]]}
        ).status_code
        == 422
    )
    assert (
        client.post("/api/duplicates/resolve", json={**body, "expected_hashes": {}}).status_code
        == 422
    )
    client.cookies.clear()
    assert client.get("/api/duplicates").status_code == 401
    assert client.post("/api/duplicates/scan").status_code == 401
    assert client.post("/api/duplicates/resolve", json=body).status_code == 401


def test_resolve_revalidates_after_hashing_without_partial_deletion(
    client, settings, tmp_path, monkeypatch
):
    from reelvault.api import duplicates as api
    from reelvault.models import Video

    source = make_video(tmp_path / "source.mp4")
    a, b = upload_ready(client, source), upload_ready(client, source)
    submit_scan(client, [a["id"], b["id"]])
    group = client.get("/api/duplicates").json()["items"][0]
    body = resolution(group)
    original = api.content_hash
    checked = 0

    async def change_after_check(path):
        nonlocal checked
        result = await original(path)
        checked += 1
        if checked == 2:
            with client.app.state.sessionmaker() as db:
                target = db.get(Video, body["keep_id"])
                target.asset_version += 1
                db.commit()
        return result

    monkeypatch.setattr(api, "content_hash", change_after_check)
    response = client.post("/api/duplicates/resolve", json=body)
    assert response.status_code == 409 and response.json()["code"] == "duplicate_result_stale"
    with client.app.state.sessionmaker() as db:
        assert db.get(Video, a["id"]).deleted_at is None
        assert db.get(Video, b["id"]).deleted_at is None


def test_similarity_is_not_transitively_merged(client, tmp_path):
    videos = [
        upload_ready(client, make_video(tmp_path / f"gray-{color}.mp4", f"color=c=0x{color}"))
        for color in ["909090", "9a9a9a", "a4a4a4"]
    ]
    ids = [v["id"] for v in videos]
    submit_scan(client, ids)
    report = client.get("/api/duplicates", params={"kind": "similar"}).json()
    assert report["total"] == 2
    assert all(len(group["videos"]) == 2 for group in report["items"])
    pairs = {frozenset(v["id"] for v in group["videos"]) for group in report["items"]}
    assert pairs == {frozenset(ids[:2]), frozenset(ids[1:])}
    from reelvault.models import Video, VideoFingerprint

    with client.app.state.sessionmaker() as db:
        hashes = {vid: db.get(VideoFingerprint, vid).sha256 for vid in ids}
    response = client.post(
        "/api/duplicates/resolve",
        json={
            "keep_id": ids[0],
            "remove_ids": ids[1:],
            "expected_hashes": hashes,
            "kind": "similar",
        },
    )
    assert response.status_code == 409 and response.json()["code"] == "duplicate_not_match"
    with client.app.state.sessionmaker() as db:
        assert all(db.get(Video, vid).deleted_at is None for vid in ids)


def test_real_scan_pause_cancel_and_rescan(client, tmp_path, monkeypatch):
    import importlib
    import threading

    from reelvault.models import VideoFingerprint

    module = importlib.import_module("reelvault.jobs.duplicates")
    video = upload_ready(client, make_video(tmp_path / "source.mp4"))
    entered = threading.Event()
    release = threading.Event()
    original = module.visual_fingerprint

    async def wait_for_control(*args, handle=None, **kwargs):
        entered.set()
        while not release.is_set():
            await handle.checkpoint()
            await asyncio.sleep(0.01)
        return await original(*args, handle=handle, **kwargs)

    monkeypatch.setattr(module, "visual_fingerprint", wait_for_control)
    first = client.post("/api/duplicates/scan").json()
    assert entered.wait(5)
    assert client.post(f"/api/jobs/{first['id']}/pause").status_code == 200
    assert client.get(f"/api/jobs/{first['id']}").json()["status"] == "paused"
    assert client.post(f"/api/jobs/{first['id']}/cancel").status_code == 200
    assert wait_job(client, first["id"])["status"] == "canceled"
    with client.app.state.sessionmaker() as db:
        assert db.get(VideoFingerprint, video["id"]) is None
    # Resume controls apply to a fresh scan, preserving the canceled job's audit record.
    entered.clear()
    second = client.post("/api/duplicates/scan").json()
    assert second["id"] != first["id"] and entered.wait(5)
    assert client.post(f"/api/jobs/{second['id']}/pause").status_code == 200
    release.set()
    assert client.post(f"/api/jobs/{second['id']}/resume").status_code == 200
    assert wait_job(client, second["id"])["status"] == "succeeded"
