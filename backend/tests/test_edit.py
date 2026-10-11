from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from reelvault.config import Settings
from reelvault.media import derive, ops
from reelvault.media.ops import atempo_chain
from reelvault.media.probe import MediaInfo

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


def test_mixed_rotate_compress(client: TestClient, samples: dict[str, Path]) -> None:
    source = upload_ready(client, samples["long"])
    edit = {
        "op": "mixed",
        "rotate": {"op": "rotate", "angle": 90, "flip": "horizontal"},
        "compress": {"op": "compress", "resolution": 360, "quality": "low"},
    }
    job = run_edit(client, source["id"], edit)
    v = result_video(client, job)
    assert (v["width"], v["height"]) == (360, 640)
    assert v["size"] < source["size"]
    assert v["audio_codec"] == "aac"
    assert v["title"].endswith("(混合编辑)")
    assert job["params"]["edit"]["rotate"]["angle"] == 90


@pytest.mark.parametrize("mute", [False, True])
def test_mixed_trim_crop_speed_target(
    client: TestClient, clip: dict[str, Any], mute: bool
) -> None:
    edit = {
        "op": "mixed",
        "trim": {"start": 1, "end": 3},
        "crop": {"op": "crop", "x": 10, "y": 20, "width": 200, "height": 100},
        "rotate": {"op": "rotate", "angle": 90},
        "speed": {"op": "speed", "factor": 2},
        "mute": mute,
        "compress": {"op": "compress", "target_size_mb": 0.15, "preset": "veryfast"},
    }
    v = result_video(client, run_edit(client, clip["id"], edit))
    assert (v["width"], v["height"]) == (100, 200)
    assert 0.8 < v["duration"] < 1.3
    assert v["audio_codec"] == (None if mute else "aac")
    assert v["size"] < 0.15 * 1.2 * 1024 * 1024


def test_mixed_invalid_params(client: TestClient, clip: dict[str, Any]) -> None:
    for params in (
        {},
        {"rotate": {"op": "rotate", "angle": 45}},
        {"trim": {"start": 3, "end": 1}},
        {"speed": {"op": "speed", "factor": 0}},
    ):
        r = client.post(f"/api/videos/{clip['id']}/edit", json={"edit": {"op": "mixed", **params}})
        assert r.status_code == 422
    edit = {"op": "mixed", "crop": {"op": "crop", "x": 0, "y": 0, "width": 999, "height": 100}}
    job_id = client.post(f"/api/videos/{clip['id']}/edit", json={"edit": edit}).json()["id"]
    assert wait_job(client, job_id)["status"] == "failed"


@pytest.mark.parametrize("target", [None, 1.0])
def test_mixed_plan_encodes_filters_together(tmp_path: Path, target: float | None) -> None:
    info = MediaInfo(width=1920, height=1080, duration=10, fps=30, audio_codec="aac", audio_index=1)
    params = ops.MixedParams(
        crop=ops.CropParams(x=0, y=0, width=1280, height=720),
        rotate=ops.RotateParams(angle=90),
        speed=ops.SpeedParams(factor=0.5),
        compress=ops.CompressParams(resolution=360, target_size_mb=target),
    )
    plan = ops.plan_mixed(params, (tmp_path / "source.mp4", info), tmp_path / "out.mp4", tmp_path)
    assert len(plan.commands) == (1 if target is None else 2)
    assert plan.duration == 20
    for command in plan.commands:
        assert command.count("-i") == 1
        filters = command[command.index("-vf") + 1]
        assert filters == (
            "crop=1280:720:0:0,transpose=clock,setpts=PTS/0.5,scale=360:640,format=yuv420p"
        )
    assert (info.width, info.height, info.duration) == (1920, 1080, 10)
    assert "-af" in plan.commands[-1]


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


@pytest.mark.parametrize("sample", ["a", "mkv"])
def test_replace_mode_keeps_id_and_backs_up(
    client: TestClient, settings: Settings, samples: dict[str, Path], sample: str
) -> None:
    clip = upload_ready(client, samples[sample])
    assert client.post(f"/api/videos/{clip['id']}/cover", json={"time": 1.0}).status_code == 200
    clip = client.get(f"/api/videos/{clip['id']}").json()
    old_assets = settings.derived_dir / clip["id"]
    assets = {
        name: (old_assets / name).read_bytes()
        for name in (derive.POSTER, derive.PREVIEW, derive.SPRITE, derive.VTT, derive.PLAYABLE)
        if (old_assets / name).is_file()
    }
    job = run_edit(client, clip["id"], {"op": "rotate", "angle": 90}, {"mode": "replace"})
    assert job["result_video_id"] == clip["id"]
    v = wait_ready(client, clip["id"])
    assert (v["width"], v["height"]) == (240, 320)
    trash = client.get("/api/videos", params={"trash": True}).json()["items"]
    assert [t["title"] for t in trash] == [f"{clip['title']} (编辑前)"]
    backup = trash[0]
    assert backup["status"] == "ready"
    assert (backup["width"], backup["height"], backup["duration"]) == (
        clip["width"], clip["height"], clip["duration"]
    )
    assert client.get("/api/jobs", params={"video_id": backup["id"]}).json() == []
    for name, content in assets.items():
        if name == derive.VTT:
            content = content.replace(clip["id"].encode(), backup["id"].encode())
        assert (settings.derived_dir / backup["id"] / name).read_bytes() == content
    restored = client.post(f"/api/videos/{backup['id']}/restore").json()
    for key in ("poster_url", "preview_url", "thumbnails_url"):
        assert restored[key]
        assert client.get(restored[key]).status_code == 200
    assert client.get(restored["stream_url"]).status_code == 200
    if sample == "mkv":
        assert derive.PLAYABLE in assets
        assert client.get(f"/api/videos/{backup['id']}/playback-cache").json()["cached"]


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
