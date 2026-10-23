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


def test_fast_trim_matroska_with_b_frames(client: TestClient, samples: dict[str, Path]) -> None:
    source = upload_ready(client, samples["mkv"])
    # AAC priming can shift Matroska keyframes slightly past whole seconds.
    # Request points inside the GOP so both timestamp layouts snap to the same GOP.
    edit = {"op": "trim", "mode": "fast", "segments": [{"start": 1.1, "end": 1.9}]}
    job = run_edit(client, source["id"], edit)
    segment = job["params"]["edit"]["segments"][0]
    assert 0.99 <= segment["start"] <= 1.1
    assert 1.9 <= segment["end"] <= 2.1
    v = result_video(client, job)
    # Without compensation the copy starts at the keyframe before the snapped one.
    assert v["container"] == "mkv"
    assert 0.9 < v["duration"] < 1.4


def test_fast_trim_seek_compensation(tmp_path: Path) -> None:
    params = ops.TrimParams(mode="fast", segments=[ops.Segment(start=1, end=3)])

    def seek(**fields: Any) -> tuple[float, float]:
        info = MediaInfo(duration=4, audio_codec="aac", audio_index=1, **fields)
        args = ops.plan_trim(params, (tmp_path / "in", info), tmp_path / "out.mp4").commands[0]
        return float(args[args.index("-ss") + 1]), float(args[args.index("-t") + 1])

    start, length = seek(container="mkv", video_has_b_frames=True)
    assert start == pytest.approx(1 + 3 / 23 + 0.001) and start + length == pytest.approx(3)
    assert seek(container="mkv") == (1, 2)
    assert seek(container="mp4", video_has_b_frames=True) == (1, 2)


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
    edit = {
        "op": "merge",
        "video_ids": [a["id"], p["id"]],
        "mode": "lossless",
        "delete_sources": True,
    }
    r = client.post(f"/api/videos/{a['id']}/edit", json={"edit": edit})
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "failed" and "无法无损合并" in job["error"]
    for source in (a, p):
        assert client.get(f"/api/videos/{source['id']}").status_code == 200


@pytest.mark.parametrize("delete_sources", [None, False, True])
def test_merge_originals_option(client, samples, delete_sources):
    originals = [upload_ready(client, samples[key]) for key in ("a", "b", "portrait")]
    edit = {"op": "merge", "video_ids": [v["id"] for v in originals]}
    if delete_sources is not None:
        edit["delete_sources"] = delete_sources
    merged = result_video(client, run_edit(client, originals[0]["id"], edit))
    active = {v["id"] for v in client.get("/api/videos").json()["items"]}
    trash = {v["id"] for v in client.get("/api/videos", params={"trash": True}).json()["items"]}
    assert merged["id"] in active
    for source in originals:
        assert (source["id"] in trash) == bool(delete_sources)
        assert (source["id"] in active) != bool(delete_sources)
    if delete_sources:
        # Restore every input, then replay history without retiring them again.
        for source in originals:
            assert client.post(f"/api/videos/{source['id']}/restore").status_code == 200
        replay = client.post(f"/api/videos/{merged['id']}/recreate")
        assert replay.status_code == 200
        replay_job = wait_job(client, replay.json()["id"])
        result_video(client, replay_job)
        for source in originals:
            assert client.get(f"/api/videos/{source['id']}").status_code == 200


def test_merge_keeps_originals_when_result_processing_fails(client, samples, monkeypatch):
    originals = [upload_ready(client, samples[key]) for key in ("a", "b")]

    async def fail_preview(*args, **kwargs):
        raise RuntimeError("preview failed")

    monkeypatch.setattr(derive, "make_preview", fail_preview)
    job = run_edit(
        client,
        originals[0]["id"],
        {
            "op": "merge",
            "video_ids": [v["id"] for v in originals],
            "delete_sources": True,
        },
    )
    ingestion = next(
        j
        for j in client.get("/api/jobs").json()
        if j["kind"] == "ingest" and j["video_ids"] == [job["result_video_id"]]
    )
    assert wait_job(client, ingestion["id"])["status"] == "failed"
    for source in originals:
        assert client.get(f"/api/videos/{source['id']}").status_code == 200


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


def test_compress_av1_crf_and_target_size(client: TestClient, samples: dict[str, Path]) -> None:
    features = client.get("/api/system/info").json()["ffmpeg_features"]
    if not features["av1"]:
        pytest.skip("FFmpeg build lacks libsvtav1")
    long = upload_ready(client, samples["long"])
    edit = {"op": "compress", "codec": "av1", "preset": "ultrafast", "resolution": 360}
    v = result_video(client, run_edit(client, long["id"], edit))
    assert v["video_codec"] == "av1" and v["height"] == 360
    if features.get("av1_two_pass") is False:
        response = client.post(
            f"/api/videos/{long['id']}/edit",
            json={"edit": {**edit, "target_size_mb": 0.4, "audio_bitrate": 64}},
        )
        assert response.status_code == 400
        assert response.json()["code"] == "av1_two_pass_unsupported"
        return
    job = run_edit(client, long["id"], {**edit, "target_size_mb": 0.4, "audio_bitrate": 64})
    assert job["params"]["encoding"]["encoder"] == "libsvtav1"
    v = result_video(client, job)
    assert v["video_codec"] == "av1"
    assert v["size"] < 0.4 * 1.2 * 1024 * 1024


def test_av1_plan_maps_presets_without_maxrate(tmp_path: Path) -> None:
    info = MediaInfo(width=1280, height=720, duration=10, fps=30, video_codec="hevc")
    info.pix_fmt = "yuv420p10le"
    params = ops.CompressParams(codec="av1", preset="medium", target_size_mb=5)
    pass1, pass2 = ops.plan_compress(
        params, (tmp_path / "in", info), tmp_path / "o.mp4", tmp_path
    ).commands
    for command in (pass1, pass2):
        assert command[command.index("-c:v") + 1] == "libsvtav1"
        assert command[command.index("-preset") + 1] == str(ops.SVT_PRESETS["medium"])
        assert "-maxrate" not in command and "-pass" in command
        assert command[command.index("-pix_fmt") + 1] == "yuv420p10le"
    crf = ops.plan_compress(
        ops.CompressParams(codec="av1"), (tmp_path / "in", info), tmp_path / "o.mp4", tmp_path
    ).commands[0]
    assert crf[crf.index("-crf") + 1] == str(ops.CRF_TABLE["av1"]["medium"])


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
def test_mixed_trim_crop_speed_target(client: TestClient, clip: dict[str, Any], mute: bool) -> None:
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
        clip["width"],
        clip["height"],
        clip["duration"],
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
