from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from reelvault.media import capabilities as capability_module
from reelvault.media import ops
from reelvault.media.capabilities import (
    FEATURES,
    Capabilities,
    detect_av1_two_pass,
    detect_capabilities,
    missing_components,
    required_features,
)
from reelvault.media.ffmpeg import FFmpegError

from .conftest import upload_ready, wait_job

edit = TypeAdapter(ops.EditParams).validate_python


@pytest.mark.parametrize("stats", [None, b"", b"first-pass statistics"])
def test_av1_probe_requires_nonempty_stats_and_checks_second_pass(monkeypatch, stats):
    calls = []

    async def run(args, **kwargs):
        calls.append(args)
        if args[args.index("-pass") + 1] == "1" and stats is not None:
            Path(args[args.index("-passlogfile") + 1] + "-0.log").write_bytes(stats)

    monkeypatch.setattr(capability_module, "run_command", run)
    assert asyncio.run(detect_av1_two_pass("ffmpeg")) is bool(stats)
    assert len(calls) == (2 if stats else 1)
    assert not Path(calls[0][calls[0].index("-passlogfile") + 1]).parent.exists()


def test_av1_probe_rejects_unreadable_second_pass(monkeypatch):
    async def run(args, **kwargs):
        if args[args.index("-pass") + 1] == "1":
            Path(args[args.index("-passlogfile") + 1] + "-0.log").write_bytes(b"stats")
        else:
            raise FFmpegError("cannot read stats")

    monkeypatch.setattr(capability_module, "run_command", run)
    assert asyncio.run(detect_av1_two_pass("ffmpeg")) is False


def test_av1_target_size_requires_two_pass_support(client, samples):
    video = upload_ready(client, samples["a"])
    client.app.state.jobs.capabilities = Capabilities(encoders={"libsvtav1"}, av1_two_pass=False)
    compression = {"op": "compress", "codec": "av1", "target_size_mb": 5}
    mixed = {"op": "mixed", "rotate": {"op": "rotate", "angle": 90}, "compress": compression}
    for params in [compression, mixed]:
        response = client.post(f"/api/videos/{video['id']}/edit", json={"edit": params})
        assert response.status_code == 400
        assert response.json()["code"] == "av1_two_pass_unsupported"
        batch = client.post("/api/jobs/batch", json={"video_ids": [video["id"]], "edit": params})
        assert batch.status_code == 400
        assert batch.json()["code"] == "av1_two_pass_unsupported"
    assert (
        missing_components(
            client.app.state.jobs.capabilities, edit({"op": "compress", "codec": "av1"})
        )
        == []
    )
    assert client.get("/api/system/info").json()["ffmpeg_features"]["av1_two_pass"] is False


def test_detects_real_filters_and_encoders() -> None:
    capabilities = asyncio.run(detect_capabilities("ffmpeg"))
    assert capabilities is not None
    assert {"scale", "loudnorm", "bwdif"} <= capabilities.filters
    assert {"libx264", "aac"} <= capabilities.encoders
    assert capabilities.status()["deinterlace"] is True
    assert asyncio.run(detect_capabilities("/nonexistent/ffmpeg")) is None


def test_required_features_follow_edit_options() -> None:
    assert required_features(edit({"op": "rotate"})) == []
    assert required_features(edit({"op": "adjust", "stabilize": True, "denoise": 2})) == [
        "stabilize",
        "denoise",
    ]
    assert required_features(edit({"op": "audio", "normalize": True})) == ["loudness"]
    assert required_features(edit({"op": "watermark", "text": "hi"})) == ["text_watermark"]
    assert required_features(edit({"op": "animation", "format": "webp", "end": 1})) == ["webp"]
    assert required_features(edit({"op": "compress", "codec": "h265"})) == ["h265"]
    assert required_features(edit({"op": "compress", "codec": "av1"})) == ["av1"]
    assert required_features(edit({"op": "compress"})) == []
    assert required_features(edit({"op": "convert", "format": "webm"})) == ["webm"]
    assert all(feature in FEATURES for feature in ("stabilize", "loudness", "webp"))


def test_missing_components_lists_each_name_once() -> None:
    capabilities = Capabilities(filters={"vidstabdetect"}, encoders={"libx264"})
    stabilize = edit({"op": "adjust", "stabilize": True})
    assert missing_components(capabilities, stabilize) == ["vidstabtransform"]
    assert missing_components(None, stabilize) == []
    assert capabilities.status()["stabilize"] is False


def test_submission_rejects_missing_components(
    client: TestClient, samples: dict[str, Path]
) -> None:
    video = upload_ready(client, samples["a"])
    jobs = client.app.state.jobs
    jobs.capabilities = Capabilities(filters={"scale"}, encoders={"libx264", "aac"})
    response = client.post(
        f"/api/videos/{video['id']}/edit", json={"edit": {"op": "audio", "normalize": True}}
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "ffmpeg_component_missing"
    assert body["params"] == {"components": "loudnorm"}
    batch = client.post(
        "/api/jobs/batch",
        json={"video_ids": [video["id"]], "edit": {"op": "compress", "codec": "h265"}},
    )
    assert batch.status_code == 400 and batch.json()["params"] == {"components": "libx265"}
    # Edits without optional components are unaffected.
    rotate = client.post(f"/api/videos/{video['id']}/edit", json={"edit": {"op": "rotate"}})
    assert rotate.status_code == 200
    # Without -display_rotation a pure rotation is re-encoded instead of rejected.
    job = wait_job(client, rotate.json()["id"])
    assert job["status"] == "succeeded", job["error"]
    assert job["params"]["encoding"]["encoder"] == "libx264"
    info = client.get("/api/system/info").json()
    assert info["ffmpeg_features"]["loudness"] is False
    assert info["ffmpeg_features"]["deinterlace"] is False
    assert info["ffmpeg_features"]["lossless_rotate"] is False


def test_detect_capabilities_reads_command_line_options() -> None:
    capabilities = asyncio.run(detect_capabilities("ffmpeg"))
    assert capabilities is not None
    assert {"-filter_complex", "-map"} <= capabilities.options
    assert Capabilities(options={"-display_rotation"}).status()["lossless_rotate"] is True
