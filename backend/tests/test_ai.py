from __future__ import annotations

import json
import math
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from reelvault.ai import FACE_MODEL_ID, AiParams, face_values
from reelvault.backup import restore_backup
from reelvault.config import Settings
from reelvault.library import abs_path
from reelvault.main import create_app
from reelvault.models import Video
from reelvault.vision import MODEL_ID

from .conftest import HEADERS, login, upload_ready, wait_job

TOKEN = "test-ai-token-0123456789abcdef0123456789abcdef"


def vector(size, index=0):
    return [float(i == index) for i in range(size)]


@pytest.fixture
def service(settings):
    state = SimpleNamespace(fail=False, delay=0, calls=0, twin=False)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.reply({"ready": True, "face_model": FACE_MODEL_ID, "face_dimension": 128})

        def do_POST(self):
            assert self.headers["Authorization"] == f"Bearer {TOKEN}"
            data = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/embed/text":
                labels = json.loads(data)["texts"]
                self.reply({"vectors": [vector(512, 0 if name == "red" else 1) for name in labels]})
            elif self.path == "/embed/image":
                self.reply({"vectors": [vector(512)]})
            else:
                assert self.path == "/embed/faces" and data.startswith(b"\xff\xd8")
                state.calls += 1
                time.sleep(state.delay)
                self.reply(
                    {
                        "face_model": FACE_MODEL_ID,
                        "face_dimension": 128,
                        "faces": [
                            {
                                "box": [0.1 + i * 0.4, 0.1, 0.2, 0.3],
                                "score": 0.99,
                                "vector": [0.0] * 128
                                if state.fail
                                else vector(128, 0 if state.twin else i),
                            }
                            for i in range(2)
                        ],
                    }
                )

        def reply(self, extra):
            data = json.dumps(
                {"protocol": 1, "model": MODEL_ID, "dimension": 512, **extra}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    settings.vision_enabled = settings.ai_enabled = settings.ai_faces_enabled = True
    settings.vision_token, settings.vision_url = TOKEN, f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()
    server.server_close()
    worker.join(5)


def index(client, video):
    job = client.post(f"/api/videos/{video['id']}/visual-index", json={"interval": 1}).json()
    assert wait_job(client, job["id"])["status"] == "succeeded"


def analyse(client, video, **params):
    response = client.post(
        f"/api/videos/{video['id']}/ai-analysis",
        json={"candidates": ["red", "blue"], "faces": True, **params},
    )
    assert response.status_code == 200, response.text
    job = wait_job(client, response.json()["id"])
    assert job["status"] == "succeeded", job["error"]
    return job


def test_ai_disabled_and_authenticated(client, samples):
    video = upload_ready(client, samples["a"])
    assert not client.get("/api/ai/status").json()["enabled"]
    assert (
        client.post(f"/api/videos/{video['id']}/ai-analysis", json={}).json()["code"]
        == "ai_disabled"
    )
    assert client.get("/api/ai/faces").status_code == 403
    client.cookies.clear()
    assert client.get("/api/ai/status").status_code == 401


def test_scene_suggestions_face_groups_review_and_manual_corrections(
    client, samples, service, settings
):
    videos = [upload_ready(client, samples[key]) for key in ("a", "b")]
    for video in videos:
        index(client, video)
        analyse(client, video)
    status = client.get(f"/api/videos/{videos[0]['id']}/ai-analysis").json()
    assert [row["name"] for row in status["analysis"]["suggestions"]] == ["red"]
    assert client.get(status["analysis"]["suggestions"][0]["thumbnail"]).status_code == 200
    assert not client.get(f"/api/videos/{videos[0]['id']}").json()["tags"]
    url = f"/api/videos/{videos[0]['id']}/ai-analysis/tags"
    assert (
        client.post(url, json={"generation": status["generation"], "names": ["blue"]}).status_code
        == 422
    )
    assert (
        client.post(url, json={"generation": status["generation"], "names": ["red"]}).status_code
        == 200
    )
    assert client.get(f"/api/videos/{videos[0]['id']}").json()["tags"] == ["red"]
    groups = client.get("/api/ai/face-groups").json()["items"]
    assert len(groups) == 2 and sorted(row["count"] for row in groups) == [7, 7]
    source, target = groups[0]["id"], groups[1]["id"]
    assert client.patch(f"/api/ai/face-groups/{target}", json={"name": "家人"}).status_code == 200
    faces = client.get("/api/ai/faces", params={"video_id": videos[0]["id"]}).json()["items"]
    moved, ignored = faces[0], faces[1]
    assert (
        client.post(
            "/api/ai/faces/assign", json={"ids": [moved["id"]], "group_id": target}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/ai/faces/assign", json={"ids": [ignored["id"]], "ignore": True}
        ).status_code
        == 200
    )
    analyse(client, videos[0])
    updated = client.get("/api/ai/faces", params={"video_id": videos[0]["id"]}).json()["items"]
    same = next(
        row
        for row in updated
        if row["frame_id"] == moved["frame_id"] and row["box"] == moved["box"]
    )
    assert same["group_id"] == target and same["manual"]
    hidden = client.get(
        "/api/ai/faces", params={"video_id": videos[0]["id"], "ignored": True}
    ).json()["items"]
    assert len(hidden) == 1 and hidden[0]["manual"]
    analyse(client, videos[0], faces=False)
    assert (
        client.get("/api/ai/faces", params={"video_id": videos[0]["id"], "ignored": True}).json()[
            "total"
        ]
        == 1
    )
    assert (
        client.post(f"/api/ai/face-groups/{source}/merge", json={"target_id": target}).status_code
        == 200
    )
    # The deliberate manual mismatch may split subsequent automatic observations.
    # Merge all remaining groups explicitly, rather than assuming forced propagation.
    for other in client.get("/api/ai/face-groups").json()["items"]:
        if other["id"] != target:
            assert (
                client.post(
                    f"/api/ai/face-groups/{other['id']}/merge", json={"target_id": target}
                ).status_code
                == 200
            )
    assert len(client.get("/api/ai/face-groups").json()["items"]) == 1
    with client.app.state.sessionmaker() as db:
        path = abs_path(settings, db.get(Video, videos[0]["id"]).file_path)
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1000000))
    assert client.get(f"/api/videos/{videos[0]['id']}/ai-analysis").json()["stale"]
    assert (
        client.post(url, json={"generation": status["generation"], "names": ["red"]}).json()["code"]
        == "ai_stale"
    )
    assert client.get("/api/ai/faces", params={"video_id": videos[0]["id"]}).json()["total"] == 0
    assert client.get("/api/ai/face-groups").json()["items"][0]["count"] == 6


def test_same_frame_faces_are_separate_even_with_equal_features(client, samples, service):
    service.twin = True
    video = upload_ready(client, samples["b"])
    index(client, video)
    analyse(client, video)
    groups = client.get("/api/ai/face-groups").json()["items"]
    assert len(groups) == 2 and sorted(group["count"] for group in groups) == [3, 3]


def test_failed_analysis_preserves_previous_retry_and_index_rebuild_cascades(
    client, samples, service
):
    video = upload_ready(client, samples["b"])
    index(client, video)
    analyse(client, video)
    url = f"/api/videos/{video['id']}/ai-analysis"
    old = client.get(url).json()["analysis"]
    service.fail = True
    job = client.post(url, json={"faces": True}).json()
    assert wait_job(client, job["id"])["status"] == "failed"
    assert client.get(url).json()["analysis"] == old
    service.fail = False
    retried = client.post(f"/api/jobs/{job['id']}/retry").json()
    assert wait_job(client, retried["id"])["status"] == "succeeded"
    index(client, video)
    assert client.get(url).json()["stale"]
    assert client.get("/api/ai/faces").json()["total"] == 0
    assert client.get("/api/ai/face-groups").json()["total"] == 0
    analyse(client, video)
    assert client.delete(url).status_code == 200
    assert client.get("/api/ai/faces").json()["total"] == 0


def test_ai_real_worker_pause_resume_cancel_parallel_and_index_guard(
    client, samples, service, settings
):
    video, other = upload_ready(client, samples["a"]), upload_ready(client, samples["b"])
    index(client, video)
    service.delay = 0.15
    url = f"/api/videos/{video['id']}/ai-analysis"
    job = client.post(url, json={"faces": True}).json()
    assert client.post(url, json={}).json()["id"] == job["id"]
    deadline = time.monotonic() + 10
    while not service.calls and time.monotonic() < deadline:
        time.sleep(0.02)
    assert service.calls
    assert client.post(f"/api/jobs/{job['id']}/pause").json()["status"] == "paused"
    time.sleep(0.2)
    frozen = service.calls
    time.sleep(0.2)
    assert service.calls == frozen
    assert client.get("/healthz").status_code == 200
    assert client.delete(f"/api/videos/{video['id']}/visual-index").json()["code"] == "ai_busy"
    assert (
        client.post(f"/api/videos/{video['id']}/visual-index", json={}).json()["code"] == "ai_busy"
    )
    other_job = client.post(
        f"/api/videos/{other['id']}/edit",
        json={"edit": {"op": "compress", "codec": "h264", "crf": 30}},
    ).json()
    assert wait_job(client, other_job["id"])["status"] == "succeeded"
    assert client.post(f"/api/jobs/{job['id']}/resume").status_code == 200
    client.post(f"/api/jobs/{job['id']}/cancel")
    assert wait_job(client, job["id"])["status"] == "canceled"
    assert client.get(url).json()["analysis"] is None
    assert not list(settings.tmp_dir.glob(f"job-{job['id']}"))


def test_ai_input_and_face_protocol_bounds():
    for labels in ([""], ["red", "red"], ["a" * 65], ["bad\nlabel"], ["a"] * 33):
        with pytest.raises(ValidationError):
            AiParams(candidates=labels)
    with pytest.raises(ValidationError):
        AiParams(faces="true")
    for box, values in (([0, 0, 2, 1], vector(128)), ([0, 0, 1, 1], [0] * 128)):
        with pytest.raises(ValueError):
            face_values({"box": box, "score": 0.99, "vector": values})


def test_complete_link_rejects_transitive_face_bridge(tmp_path):
    from reelvault.media.ai_worker import cluster

    snapshot = tmp_path / "groups.sqlite"
    with sqlite3.connect(snapshot) as db:
        db.executescript(
            "CREATE TABLE refs (id TEXT, group_id TEXT, frame_key TEXT, embedding BLOB);"
            "CREATE TABLE groups (group_id TEXT, prototype BLOB);"
            "CREATE TABLE fixed (frame_id INTEGER, ordinal INTEGER, group_id TEXT,"
            " ignored INTEGER);"
        )
    faces = []
    # A resembles B, B resembles C, but A and C represent different identities.
    for index, angle in enumerate([0, 40, 80]):
        values = [math.cos(math.radians(angle)), math.sin(math.radians(angle))] + [0.0] * 126
        faces.append({"id": str(index), "frame_id": index, "ordinal": 0, "vector": values})
    data = {"faces": faces}
    cluster(
        {
            "body": {"face_threshold": 0.65},
            "snapshot": str(snapshot),
            "video_id": "v",
            "generation": "g",
        },
        data,
    )
    assert faces[0]["group_id"] == faces[1]["group_id"]
    assert faces[2]["group_id"] != faces[0]["group_id"]


def test_concurrent_group_change_rematches_without_repeat_inference(client, samples, service):
    video = upload_ready(client, samples["a"])
    index(client, video)
    analyse(client, video)
    group = client.get("/api/ai/face-groups").json()["items"][0]
    detail = client.get(f"/api/ai/face-groups/{group['id']}")
    assert detail.status_code == 200 and detail.json()["count"] == 4
    before = service.calls
    service.delay = 0.1
    job = client.post(f"/api/videos/{video['id']}/ai-analysis", json={"faces": True}).json()
    deadline = time.monotonic() + 10
    while service.calls == before and time.monotonic() < deadline:
        time.sleep(0.02)
    assert service.calls > before
    assert (
        client.patch(
            f"/api/ai/face-groups/{group['id']}", json={"name": "Reviewed during inference"}
        ).status_code
        == 200
    )
    assert wait_job(client, job["id"])["status"] == "succeeded"
    assert service.calls - before == 4
    assert (
        client.get(f"/api/ai/face-groups/{group['id']}").json()["name"]
        == "Reviewed during inference"
    )


def test_source_change_rejects_publication(client, samples, service, settings):
    from reelvault.models import AiAnalysis

    video = upload_ready(client, samples["a"])
    index(client, video)
    analyse(client, video)
    with client.app.state.sessionmaker() as db:
        old = db.get(AiAnalysis, video["id"]).suggestions
        source = abs_path(settings, db.get(Video, video["id"]).file_path)
    before = service.calls
    service.delay = 0.1
    job = client.post(f"/api/videos/{video['id']}/ai-analysis", json={"faces": True}).json()
    deadline = time.monotonic() + 10
    while service.calls == before and time.monotonic() < deadline:
        time.sleep(0.02)
    assert service.calls > before
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
    assert wait_job(client, job["id"])["status"] == "failed"
    with client.app.state.sessionmaker() as db:
        assert db.get(AiAnalysis, video["id"]).suggestions == old
    assert client.get("/api/ai/faces").json()["total"] == 0
    assert not list(settings.tmp_dir.glob(f"job-{job['id']}"))


def test_ai_backup_preserves_review_and_deployment_flags(settings, samples, service, tmp_path):
    with TestClient(create_app(settings), headers=HEADERS) as client:
        login(client)
        video = upload_ready(client, samples["a"])
        index(client, video)
        analyse(client, video)
        faces = client.get("/api/ai/faces").json()["items"]
        assert (
            client.post(
                "/api/ai/faces/assign", json={"ids": [faces[0]["id"]], "name": "家人"}
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/ai/faces/assign", json={"ids": [faces[1]["id"]], "ignore": True}
            ).status_code
            == 200
        )
        archive = tmp_path / "backup.zip"
        archive.write_bytes(client.post("/api/system/backup").content)
    target = Settings(
        data_dir=tmp_path / "restored",
        update_check=False,
        admin_user="admin",
        admin_password="secret123",
    )
    target.ensure_dirs()
    for name in ("library", "derived", "exports", "assets"):
        shutil.copytree(settings.data_dir / name, target.data_dir / name, dirs_exist_ok=True)
    restored = restore_backup(archive, target)
    config = json.loads(Path(restored["config_file"]).read_text())
    assert "ai_enabled" not in config and "ai_faces_enabled" not in config
    target.ai_enabled = target.ai_faces_enabled = target.vision_enabled = True
    target.vision_url, target.vision_token = settings.vision_url, settings.vision_token
    with TestClient(create_app(target), headers=HEADERS) as client:
        login(client)
        ignored = client.get("/api/ai/faces?ignored=true").json()["items"]
        assert len(ignored) == 1 and ignored[0]["manual"]
        groups = client.get("/api/ai/face-groups?q=家人").json()["items"]
        assert len(groups) == 1 and groups[0]["count"] == 1
        assert (
            client.post(
                "/api/ai/faces/assign",
                json={"ids": [ignored[0]["id"]], "group_id": groups[0]["id"]},
            ).status_code
            == 200
        )
        assert client.get("/api/ai/faces?ignored=true").json()["total"] == 0
        assert client.delete(f"/api/ai/face-groups/{groups[0]['id']}").status_code == 200
        assert client.get("/api/ai/faces?ignored=true").json()["total"] == 2


@pytest.mark.skipif(
    not os.environ.get("REELVAULT_TEST_FACE_IMAGE")
    or not os.environ.get("REELVAULT_TEST_VISION_URL"),
    reason="Prepared private CLIP and face service required",
)
def test_actual_clip_face_analysis_cross_video(client, settings, tmp_path):
    settings.vision_enabled = settings.ai_enabled = settings.ai_faces_enabled = True
    settings.vision_url = os.environ["REELVAULT_TEST_VISION_URL"]
    settings.vision_token = os.environ["REELVAULT_TEST_VISION_TOKEN"]
    sample = tmp_path / "portrait.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-loop",
            "1",
            "-i",
            os.environ["REELVAULT_TEST_FACE_IMAGE"],
            "-t",
            "2",
            "-r",
            "10",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(sample),
        ],
        check=True,
    )
    for _ in range(2):
        video = upload_ready(client, sample)
        index(client, video)
        analyse(
            client, video, candidates=["a portrait of a person", "an empty beach"], min_score=0.1
        )
        result = client.get(f"/api/videos/{video['id']}/ai-analysis").json()["analysis"]
        assert result["suggestions"][0]["name"] == "a portrait of a person"
        assert result["faces"] == 2
        with client.app.state.sessionmaker() as db:
            assert (
                abs_path(settings, db.get(Video, video["id"]).file_path).read_bytes()
                == sample.read_bytes()
            )
    groups = client.get("/api/ai/face-groups").json()["items"]
    assert len(groups) == 1 and groups[0]["count"] == 4
