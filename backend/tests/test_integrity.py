from __future__ import annotations

import random
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from reelvault.config import Settings
from reelvault.media import ops
from reelvault.media.probe import MediaInfo
from reelvault.models import Job

from .conftest import upload_ready, wait_job, wait_ready


@pytest.fixture(scope="module")
def damaged(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """MPEG-TS with three runs of overwritten packets."""
    path = tmp_path_factory.mktemp("damaged") / "damaged.ts"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:d=6"]
        + ["-f", "lavfi", "-i", "sine=d=6", "-c:v", "libx264", "-g", "25", "-c:a", "aac"]
        + ["-f", "mpegts", str(path)],
        check=True,
    )
    data = bytearray(path.read_bytes())
    noise = random.Random(1)
    for offset in (len(data) // 3, len(data) // 2, 2 * len(data) // 3):
        for i in range(offset, offset + 188 * 6):
            data[i] = noise.randrange(256)
    path.write_bytes(data)
    return path


def integrity_jobs(client: TestClient, video_id: str) -> list[Job]:
    with client.app.state.sessionmaker() as db:
        return [
            job
            for job in db.query(Job).filter(Job.kind == "integrity")
            if video_id in job.video_ids
        ]


def check(client: TestClient, video_id: str) -> dict[str, Any]:
    response = client.post(f"/api/videos/{video_id}/integrity")
    assert response.status_code == 200, response.text
    assert wait_job(client, response.json()["id"])["status"] == "succeeded"
    return client.get(f"/api/videos/{video_id}").json()["integrity"]


def test_import_queues_a_low_priority_decode_check(
    client: TestClient, settings: Settings, damaged: Path, samples: dict[str, Path]
) -> None:
    settings.integrity_check = True
    video = upload_ready(client, damaged)
    (job,) = integrity_jobs(client, video["id"])
    assert job.priority == 0
    assert wait_job(client, job.id)["status"] == "succeeded"
    report = client.get(f"/api/videos/{video['id']}").json()["integrity"]
    assert report["errors"] > 0 and not report["aborted"]
    assert any("error while decoding" in line for line in report["sample"])
    clean = upload_ready(client, samples["a"])
    (job,) = integrity_jobs(client, clean["id"])
    wait_job(client, job.id)
    assert client.get(f"/api/videos/{clean['id']}").json()["integrity"]["errors"] == 0


def test_repair_remuxes_without_corrupt_packets(
    client: TestClient, settings: Settings, damaged: Path
) -> None:
    video = upload_ready(client, damaged)
    assert integrity_jobs(client, video["id"]) == []  # disabled in the test settings
    assert check(client, video["id"])["errors"] > 0
    response = client.post(f"/api/videos/{video['id']}/edit", json={"edit": {"op": "repair"}})
    job = wait_job(client, response.json()["id"])
    assert job["status"] == "succeeded", job["error"]
    assert job["params"]["encoding"]["encoder"] == "copy/other"
    repaired = wait_ready(client, job["result_video_id"])
    assert repaired["container"] == "mkv" and repaired["title"].endswith("(修复（重新封装）)")
    assert check(client, repaired["id"])["errors"] == 0


def test_repair_plan_and_report_reset_on_reprobe(tmp_path: Path) -> None:
    info = MediaInfo(duration=4, container="mp4", audio_codec="aac", audio_index=1)
    (command,) = ops.plan_repair((tmp_path / "in.mp4", info), tmp_path / "out.mp4").commands
    assert command[:4] == ["-err_detect", "ignore_err", "-fflags", "+genpts+discardcorrupt"]
    assert command[command.index("-c") + 1] == "copy" and "+faststart" in command
    from reelvault.library import apply_media_info
    from reelvault.models import Video

    video = Video(meta={"integrity": {"errors": 3}, "custom_cover": True})
    apply_media_info(video, info, 10)
    assert "integrity" not in video.meta and video.meta["custom_cover"]
