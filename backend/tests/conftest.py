from __future__ import annotations

import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from reelvault.config import Settings
from reelvault.main import create_app

HEADERS = {"X-Requested-With": "ReelVault"}
USER, PASSWORD = "admin", "secret123"


def make_video(
    path: Path,
    *,
    duration: float = 4,
    size: str = "320x240",
    rate: int = 25,
    audio: bool = True,
    codec: str = "libx264",
    extra: list[str] | None = None,
) -> Path:
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    args += ["-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}:duration={duration}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
    args += ["-c:v", codec, "-pix_fmt", "yuv420p", "-g", "25"]
    if audio:
        args += ["-c:a", "aac", "-shortest"]
    args += (extra or []) + [str(path)]
    subprocess.run(args, check=True)
    return path


@pytest.fixture(scope="session")
def samples(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("samples")
    return {
        "a": make_video(d / "clip_a.mp4", duration=4),
        "b": make_video(d / "clip_b.mp4", duration=3),
        "portrait": make_video(d / "portrait.mp4", duration=3, size="240x320", audio=False),
        "mkv": make_video(d / "clip.mkv", duration=3),
        "long": make_video(d / "long.mp4", duration=20, size="640x360"),
    }


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        admin_user=USER,
        admin_password=PASSWORD,
        workers=2,
        static_dir=tmp_path / "nostatic",
        login_max_failures=3,
        update_check=False,
    )


@pytest.fixture
def anon(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings), headers=HEADERS) as c:
        yield c


def login(client: TestClient, remember: bool = False, device: str = "test") -> dict[str, Any]:
    r = client.post(
        "/api/auth/login",
        json={"username": USER, "password": PASSWORD, "remember": remember, "device_name": device},
    )
    assert r.status_code == 200, r.text
    return dict(r.json())


@pytest.fixture
def client(anon: TestClient) -> TestClient:
    login(anon)
    return anon


def upload(client: TestClient, path: Path, chunk: int = 64 * 1024, folder_id: int | None = None):
    data = path.read_bytes()
    r = client.post(
        "/api/uploads", json={"filename": path.name, "size": len(data), "folder_id": folder_id}
    )
    assert r.status_code == 200, r.text
    up = r.json()
    offset = 0
    while offset < len(data):
        part = data[offset : offset + chunk]
        r = client.put(f"/api/uploads/{up['id']}?offset={offset}", content=part)
        assert r.status_code == 200, r.text
        offset += len(part)
    r = client.post(f"/api/uploads/{up['id']}/complete")
    assert r.status_code == 200, r.text
    return r.json()


def wait_job(client: TestClient, job_id: str, timeout: float = 90) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed", "canceled"):
            return dict(job)
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} timed out")


def wait_ready(client: TestClient, video_id: str, timeout: float = 90) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = client.get(f"/api/videos/{video_id}").json()
        if v["status"] in ("ready", "error"):
            assert v["status"] == "ready", v.get("error")
            return dict(v)
        time.sleep(0.2)
    raise AssertionError(f"video {video_id} not ready")


def upload_ready(client: TestClient, path: Path, **kw: Any) -> dict[str, Any]:
    return wait_ready(client, upload(client, path, **kw)["id"])
