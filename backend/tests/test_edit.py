from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from reelvault.media.ops import atempo_chain

from .conftest import upload_ready, wait_job, wait_ready


def run_edit(
    client: TestClient, video_id: str, edit: dict[str, Any], output: dict[str, Any] | None = None
) -> dict[str, Any]:
    r = client.post(f"/api/videos/{video_id}/edit", json={"edit": edit, "output": output or {}})
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job["error"]
    return job


def result_video(client: TestClient, job: dict[str, Any]) -> dict[str, Any]:
    assert job["result_video_id"]
    return wait_ready(client, job["result_video_id"])


@pytest.fixture
def clip(client: TestClient, samples: dict[str, Path]) -> dict[str, Any]:
    return upload_ready(client, samples["a"])


@pytest.mark.parametrize(("angle", "dims"), [(90, (240, 320)), (180, (320, 240))])
def test_rotate(client: TestClient, clip: dict[str, Any], angle: int, dims: tuple[int, int]):
    job = run_edit(client, clip["id"], {"op": "rotate", "angle": angle})
    v = result_video(client, job)
    assert (v["width"], v["height"]) == dims
    assert v["title"] == "clip_a (旋转)"


@pytest.mark.parametrize("mode", ["fast", "precise"])
def test_trim_single(client: TestClient, clip: dict[str, Any], mode: str) -> None:
    edit = {"op": "trim", "mode": mode, "segments": [{"start": 1, "end": 3}]}
    v = result_video(client, run_edit(client, clip["id"], edit))
    assert 1.5 < v["duration"] < 2.6


def test_trim_multi_segment(client: TestClient, clip: dict[str, Any]) -> None:
    edit = {"op": "trim", "segments": [{"start": 0, "end": 1}, {"start": 2.5, "end": 4}]}
    v = result_video(client, run_edit(client, clip["id"], edit))
    assert 2.2 < v["duration"] < 2.8
    assert v["audio_codec"] == "aac"


def test_merge_lossless_and_reencode(client: TestClient, samples: dict[str, Path]) -> None:
    a = upload_ready(client, samples["a"])
    b = upload_ready(client, samples["b"])
    p = upload_ready(client, samples["portrait"])

    job = run_edit(client, a["id"], {"op": "merge", "video_ids": [a["id"], b["id"]]})
    v = result_video(client, job)
    assert 6.5 < v["duration"] < 7.5

    # different orientation and one input without audio -> re-encode with padding
    ids = [a["id"], p["id"]]
    job = run_edit(client, a["id"], {"op": "merge", "video_ids": ids, "mode": "auto"})
    v = result_video(client, job)
    assert (v["width"], v["height"]) == (320, 240)
    assert v["audio_codec"] == "aac"
    assert 6.5 < v["duration"] < 7.5


def test_merge_lossless_rejected_when_incompatible(
    client: TestClient, samples: dict[str, Path]
) -> None:
    a = upload_ready(client, samples["a"])
    p = upload_ready(client, samples["portrait"])
    edit = {"op": "merge", "video_ids": [a["id"], p["id"]], "mode": "lossless"}
    r = client.post(f"/api/videos/{a['id']}/edit", json={"edit": edit})
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "failed" and "无法无损合并" in job["error"]


def test_compress_resolution_and_target_size(client: TestClient, samples: dict[str, Path]) -> None:
    long = upload_ready(client, samples["long"])
    job = run_edit(client, long["id"], {"op": "compress", "resolution": 360, "quality": "low"})
    v = result_video(client, job)
    assert (v["width"], v["height"]) == (640, 360)
    assert v["size"] < long["size"]

    edit = {
        "op": "compress",
        "resolution": 360,
        "target_size_mb": 0.6,
        "audio_bitrate": 64,
        "preset": "veryfast",
    }
    v = result_video(client, run_edit(client, long["id"], edit))
    assert v["height"] == 360
    assert v["size"] < 0.6 * 1.2 * 1024 * 1024


def test_compress_h265(client: TestClient, clip: dict[str, Any]) -> None:
    edit = {"op": "compress", "codec": "h265", "preset": "ultrafast"}
    v = result_video(client, run_edit(client, clip["id"], edit))
    assert v["video_codec"] == "hevc"


def test_crop_speed_mute_convert(client: TestClient, clip: dict[str, Any]) -> None:
    v = result_video(
        client,
        run_edit(client, clip["id"], {"op": "crop", "x": 10, "y": 20, "width": 200, "height": 100}),
    )
    assert (v["width"], v["height"]) == (200, 100)

    v = result_video(client, run_edit(client, clip["id"], {"op": "speed", "factor": 2}))
    assert 1.7 < v["duration"] < 2.3

    v = result_video(client, run_edit(client, clip["id"], {"op": "mute"}))
    assert v["audio_codec"] is None

    v = result_video(client, run_edit(client, clip["id"], {"op": "convert", "format": "mkv"}))
    assert v["container"] == "mkv"


def test_extract_audio(client: TestClient, clip: dict[str, Any]) -> None:
    job = run_edit(client, clip["id"], {"op": "extract_audio", "format": "mp3"})
    assert job["has_result_file"]
    r = client.get(f"/api/jobs/{job['id']}/download")
    assert r.status_code == 200 and len(r.content) > 1000
    assert "clip_a.mp3" in r.headers["content-disposition"]


def test_replace_mode_keeps_id_and_backs_up(client: TestClient, clip: dict[str, Any]) -> None:
    job = run_edit(client, clip["id"], {"op": "rotate", "angle": 90}, {"mode": "replace"})
    assert job["result_video_id"] == clip["id"]
    v = wait_ready(client, clip["id"])
    assert (v["width"], v["height"]) == (240, 320)
    trash = client.get("/api/videos", params={"trash": True}).json()["items"]
    assert [t["title"] for t in trash] == ["clip_a (编辑前)"]
    wait_ready(client, trash[0]["id"])


def test_embed_cover(client: TestClient, clip: dict[str, Any]) -> None:
    client.post(f"/api/videos/{clip['id']}/cover", json={"time": 1.0})
    run_edit(client, clip["id"], {"op": "embed_cover"})
    v = client.get(f"/api/videos/{clip['id']}").json()
    assert v["size"] > clip["size"]
    # re-processing still picks the real video stream, not the cover image
    job_id = client.post(f"/api/videos/{clip['id']}/reprocess").json()["job_id"]
    assert wait_job(client, job_id)["status"] == "succeeded"
    assert wait_ready(client, clip["id"])["width"] == 320


def test_cancel_job(client: TestClient, samples: dict[str, Path]) -> None:
    long = upload_ready(client, samples["long"])
    edit = {"op": "convert", "format": "webm"}
    r = client.post(f"/api/videos/{long['id']}/edit", json={"edit": edit})
    job_id = r.json()["id"]
    client.post(f"/api/jobs/{job_id}/cancel")
    job = wait_job(client, job_id)
    assert job["status"] == "canceled"


def test_invalid_params(client: TestClient, clip: dict[str, Any]) -> None:
    r = client.post(
        f"/api/videos/{clip['id']}/edit",
        json={"edit": {"op": "trim", "segments": [{"start": 3, "end": 1}]}},
    )
    assert r.status_code == 422
    r = client.post(f"/api/videos/{clip['id']}/edit", json={"edit": {"op": "explode"}})
    assert r.status_code == 422


def test_atempo_chain() -> None:
    assert atempo_chain(4) == "atempo=2.0,atempo=2.000000"
    assert atempo_chain(0.25) == "atempo=0.5,atempo=0.500000"
