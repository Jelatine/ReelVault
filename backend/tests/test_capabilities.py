from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from reelvault.media import ops
from reelvault.media.capabilities import (
    FEATURES,
    Capabilities,
    detect_capabilities,
    missing_components,
    required_features,
)

from .conftest import upload_ready

edit = TypeAdapter(ops.EditParams).validate_python


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
    info = client.get("/api/system/info").json()
    assert info["ffmpeg_features"]["loudness"] is False
    assert info["ffmpeg_features"]["deinterlace"] is False
