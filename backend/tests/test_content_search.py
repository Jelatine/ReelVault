from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from reelvault.content_search import parse_vtt
from reelvault.models import SubtitleCue, SubtitleTrack

from .conftest import upload_ready, wait_job

VTT = """WEBVTT

00:00:00.250 --> 00:00:01.500
<b>Purple elephant</b> &amp; 100%_literal

00:00:02.000 --> 00:00:03.000
今天去海边散步
"""


def attach(client, video, content=VTT):
    response = client.post("/api/subtitle-assets", files={"file": ("speech.vtt", content.encode())})
    assert response.status_code == 200, response.text
    asset = response.json()
    response = client.post(
        f"/api/videos/{video['id']}/subtitles",
        json={"asset_id": asset["id"], "label": "Speech", "language": "en"},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def search(client, q, **params):
    response = client.get("/api/content-search", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_timed_subtitle_fts_short_chinese_literals_and_filters(client, samples):
    video = upload_ready(client, samples["a"])
    track = attach(client, video)
    hit = search(client, "purple elephant")
    assert hit["total"] == 1
    item = hit["items"][0]
    assert item["start"] == 0.25 and item["end"] == 1.5
    assert item["text"] == "Purple elephant & 100%_literal"
    assert item["url"] == f"/videos/{video['id']}?t=0.250"
    assert item["track_id"] == track
    assert search(client, "海边")["items"][0]["start"] == 2
    assert search(client, "今天去海边")["total"] == 1
    assert search(client, "100%_literal")["total"] == 1
    assert search(client, "%_")["total"] == 1
    assert search(client, '" OR purple')["total"] == 0
    assert search(client, "purple 海边")["total"] == 0
    assert search(client, "purple rating:>=4")["total"] == 0
    client.patch(f"/api/videos/{video['id']}", json={"rating": 5, "tags": ["旅行"]})
    assert search(client, "purple rating:>=4 tag:旅行")["total"] == 1
    assert search(client, "purple", page=2, page_size=1)["items"] == []
    assert search(client, "")["total"] == 0
    assert client.get("/api/content-search?q=purple").headers["cache-control"] == "no-store"
    assert client.get("/api/content-search", params={"q": "x" * 513}).status_code == 422
    client.cookies.clear()
    assert client.get("/api/content-search?q=purple").status_code == 401


def test_index_detach_trash_restore_source_changes_and_cascade(client, samples, settings):
    from reelvault.library import abs_path

    video = upload_ready(client, samples["a"])
    track = attach(client, video)
    client.delete(f"/api/videos/{video['id']}")
    assert search(client, "purple")["total"] == 0
    client.post(f"/api/videos/{video['id']}/restore")
    assert search(client, "purple")["total"] == 1
    path = abs_path(settings, video["file_path"]) if "file_path" in video else None
    if path is None:
        from reelvault.models import Video

        with client.app.state.sessionmaker() as db:
            path = abs_path(settings, db.get(Video, video["id"]).file_path)
    stamp = path.stat()
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1000000))
    assert search(client, "purple")["total"] == 0
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert search(client, "purple")["total"] == 1
    client.delete(f"/api/videos/{video['id']}/subtitles/{track}")
    assert search(client, "purple")["total"] == 0
    attach(client, video)
    client.delete(f"/api/videos/{video['id']}")
    client.delete(f"/api/videos/{video['id']}?permanent=true")
    with client.app.state.sessionmaker() as db:
        assert not list(db.scalars(select(SubtitleCue)))
        assert not list(db.scalars(select(SubtitleTrack)))


def test_transcription_disabled_missing_dependency_and_no_audio(
    client, samples, settings, monkeypatch
):
    import reelvault.transcription as transcription

    video = upload_ready(client, samples["a"])
    url = f"/api/videos/{video['id']}/transcription"
    assert client.get(url).json()["enabled"] is False
    assert client.post(url, json={}).json()["code"] == "transcription_disabled"
    settings.transcription_enabled = True
    monkeypatch.setattr(transcription, "available", lambda settings: False)
    assert client.post(url, json={}).json()["code"] == "transcription_dependency_missing"
    monkeypatch.setattr(transcription, "available", lambda settings: True)
    silent = upload_ready(client, samples["portrait"])
    assert (
        client.post(f"/api/videos/{silent['id']}/transcription", json={}).json()["code"]
        == "transcription_no_audio"
    )
    assert client.post(url, json={"language": "arbitrary"}).status_code == 422
    assert client.post(url, json={"priority": True}).status_code == 422


def controlled_worker(monkeypatch, tmp_path, *, slow=False, invalid=False, empty=False):
    import reelvault.jobs.transcription as handler
    import reelvault.transcription as transcription

    monkeypatch.setattr(transcription, "available", lambda settings: True)
    original = handler.run_command
    worker = tmp_path / "whisper_contract.py"
    counter = tmp_path / "progress.txt"
    counter.unlink(missing_ok=True)
    worker.write_text(f"""
import json, sys, time
from pathlib import Path
for n in range({200 if slow else 1}):
    Path({str(counter)!r}).write_text(str(n))
    print('out_time_us=' + str(250000 + n * 10000), flush=True)
    time.sleep({0.03 if slow else 0.01})
result = {{'language':'en','cues':[{{'start':0.25,'end':1.5,'text':'Purple elephant'}}]}}
if {empty!r}: result['cues'] = []
Path(sys.argv[1]).write_text(json.dumps(result) if not {invalid!r} else 'invalid')
""")

    async def command(args, **kwargs):
        if "reelvault.media.transcription_worker" in args:
            args = [sys.executable, str(worker), args[args.index("--output") + 1]]
        return await original(args, **kwargs)

    monkeypatch.setattr(handler, "run_command", command)
    return counter


def test_transcription_pause_resume_cancel_dedup_and_parallel(
    client, samples, settings, monkeypatch, tmp_path
):
    settings.transcription_enabled = True
    counter = controlled_worker(monkeypatch, tmp_path, slow=True)
    video = upload_ready(client, samples["a"])
    other = upload_ready(client, samples["b"])
    url = f"/api/videos/{video['id']}/transcription"
    job = client.post(url, json={}).json()
    assert client.post(url, json={}).json()["id"] == job["id"]
    deadline = time.monotonic() + 10
    while not counter.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert counter.exists()
    assert client.post(f"/api/jobs/{job['id']}/pause").json()["status"] == "paused"
    time.sleep(0.1)
    frozen = counter.read_text()
    time.sleep(0.2)
    assert counter.read_text() == frozen
    other_job = client.post(
        f"/api/videos/{other['id']}/edit",
        json={"edit": {"op": "compress", "codec": "h264", "crf": 30}},
    ).json()
    assert wait_job(client, other_job["id"])["status"] == "succeeded"
    assert client.delete(url).json()["code"] == "transcription_busy"
    assert client.post(f"/api/jobs/{job['id']}/resume").status_code == 200
    deadline = time.monotonic() + 3
    while counter.read_text() == frozen and time.monotonic() < deadline:
        time.sleep(0.02)
    assert counter.read_text() != frozen
    client.post(f"/api/jobs/{job['id']}/cancel")
    assert wait_job(client, job["id"])["status"] == "canceled"
    assert client.get(url).json()["track"] is None
    assert not list(settings.tmp_dir.glob(f"job-{job['id']}"))
    assert not list(settings.assets_dir.iterdir())


def test_transcription_publish_failure_preserves_previous_retry_and_clear(
    client, samples, settings, monkeypatch, tmp_path
):
    settings.transcription_enabled = True
    controlled_worker(monkeypatch, tmp_path)
    video = upload_ready(client, samples["a"])
    url = f"/api/videos/{video['id']}/transcription"
    job = client.post(url, json={}).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "succeeded", done["error"]
    track = client.get(url).json()["track"]
    assert track["segments"] == 1 and track["language"] == "en"
    tracks = client.get(f"/api/videos/{video['id']}/subtitles").json()
    assert b"Purple elephant" in client.get(tracks[0]["url"]).content
    assert search(client, "purple")["total"] == 1
    controlled_worker(monkeypatch, tmp_path, invalid=True)
    bad = client.post(url, json={}).json()
    assert wait_job(client, bad["id"])["status"] == "failed"
    assert client.get(url).json()["track"]["id"] == track["id"]
    assert search(client, "purple")["total"] == 1
    controlled_worker(monkeypatch, tmp_path)
    retry = client.post(f"/api/jobs/{bad['id']}/retry").json()
    assert wait_job(client, retry["id"])["status"] == "succeeded"
    assert client.get(url).json()["track"]["id"] != track["id"]
    assert search(client, "purple")["total"] == 1
    client.delete(url)
    assert search(client, "purple")["total"] == 0


def test_real_whisper_transcribes_and_indexes_speech(client, settings):
    cache, speech = (
        os.environ.get("REELVAULT_TEST_WHISPER_CACHE"),
        os.environ.get("REELVAULT_TEST_SPEECH"),
    )
    if not cache or not speech:
        pytest.skip("provide prepared Whisper model and a spoken video for real inference")
    settings.transcription_enabled = True
    settings.transcription_model = "tiny"
    (settings.data_dir / "models").mkdir(exist_ok=True)
    (settings.data_dir / "models" / "whisper").symlink_to(Path(cache), target_is_directory=True)
    video = upload_ready(client, Path(speech))
    job = client.post(f"/api/videos/{video['id']}/transcription", json={"language": "en"}).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "succeeded", done["error"]
    assert done["params"]["segments"] > 0
    result = search(client, "purple elephant")
    assert result["total"] >= 1
    assert result["items"][0]["start"] >= 0
    assert result["items"][0]["video_id"] == video["id"]


def test_vtt_parser_strips_markup_and_rejects_invalid_time():
    assert parse_vtt(VTT.encode())[0]["text"] == "Purple elephant & 100%_literal"
    with pytest.raises(ValueError):
        parse_vtt(b"WEBVTT\n\n00:99.000 --> 00:99.500\nbad\n")


@pytest.mark.parametrize(
    "cue",
    [
        {"start": float("nan"), "end": 1, "text": "bad"},
        {"start": True, "end": 1, "text": "bad"},
        {"start": -1, "end": 1, "text": "bad"},
        {"start": 2, "end": 1, "text": "bad"},
        {"start": 0, "end": 1000, "text": "bad"},
        {"start": 0, "end": 1, "text": "x" * 8193},
    ],
)
def test_invalid_worker_output_cannot_publish(cue, tmp_path):
    import json

    from reelvault.transcription import read_result

    path = tmp_path / "worker.json"
    path.write_text(json.dumps({"language": "en", "cues": [cue]}))
    with pytest.raises(ValueError):
        read_result(path, 4)


def test_restart_backfills_legacy_subtitles_and_migration_round_trip(settings, samples):
    from alembic import command
    from fastapi.testclient import TestClient

    from reelvault.db import make_engine
    from reelvault.main import create_app
    from reelvault.migrate import alembic_config, upgrade

    from .conftest import HEADERS, login

    with TestClient(create_app(settings), headers=HEADERS) as client:
        login(client)
        video = upload_ready(client, samples["a"])
        attach(client, video)
    engine = make_engine(settings.db_path)
    cfg = alembic_config(str(engine.url))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0028")
        assert "generated" not in [
            row[1] for row in conn.exec_driver_sql("PRAGMA table_info(subtitle_tracks)")
        ]
    upgrade(engine)
    engine.dispose()
    with TestClient(create_app(settings), headers=HEADERS) as client:
        login(client)
        assert search(client, "purple")["total"] == 1
        assert search(client, "海边")["total"] == 1


def test_transcription_source_change_during_inference_keeps_previous_index(
    client, samples, settings, monkeypatch, tmp_path
):
    from reelvault.library import abs_path
    from reelvault.models import Video

    settings.transcription_enabled = True
    controlled_worker(monkeypatch, tmp_path)
    video = upload_ready(client, samples["a"])
    url = f"/api/videos/{video['id']}/transcription"
    job = client.post(url, json={}).json()
    assert wait_job(client, job["id"])["status"] == "succeeded"
    previous = client.get(url).json()["track"]["id"]
    counter = controlled_worker(monkeypatch, tmp_path, slow=True)
    job = client.post(url, json={}).json()
    deadline = time.monotonic() + 10
    while not counter.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert counter.exists()
    with client.app.state.sessionmaker() as db:
        path = abs_path(settings, db.get(Video, video["id"]).file_path)
    stamp = path.stat()
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1000000))
    assert wait_job(client, job["id"])["status"] == "failed"
    assert client.get(url).json()["stale"] is True
    assert search(client, "purple")["total"] == 0
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert client.get(url).json()["track"]["id"] == previous
    assert search(client, "purple")["total"] == 1


def test_upgrade_preserves_transcription_extra_and_older_rollback(tmp_path, monkeypatch):
    import subprocess

    from reelvault import updates
    from reelvault.config import Settings

    calls = []
    monkeypatch.setattr(
        updates.subprocess,
        "run",
        lambda args, **kw: calls.append(args) or subprocess.CompletedProcess(args, 0, "", ""),
    )
    settings = Settings(data_dir=tmp_path / "data", transcription_enabled=True)
    updater = updates.Updater(settings, app_dir=tmp_path)
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text('[project.optional-dependencies]\ntranscription=["faster-whisper"]\n')
    updater._uv_sync()
    assert calls[-1][-2:] == ["--extra", "transcription"]
    manifest.write_text('[project]\nname="older-reelvault"\n')
    updater._uv_sync()
    assert "--extra" not in calls[-1]


def test_silent_transcription_empty_vtt_survives_cache_cleanup(
    client, samples, settings, monkeypatch, tmp_path
):
    settings.transcription_enabled = True
    controlled_worker(monkeypatch, tmp_path, empty=True)
    video = upload_ready(client, samples["a"])
    url = f"/api/videos/{video['id']}/transcription"
    job = client.post(url, json={}).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "succeeded" and done["params"]["segments"] == 0
    assert client.get(url).json()["track"]["segments"] == 0
    track = client.get(f"/api/videos/{video['id']}/subtitles").json()[0]
    (settings.assets_dir / f"{track['asset_id']}.webvtt").unlink()
    assert client.get(track["url"]).text.startswith("WEBVTT")
    assert search(client, "purple")["total"] == 0
