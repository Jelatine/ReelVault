from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from reelvault.media.probe import MediaInfo
from reelvault.media.quality import metric, parse_quality, quality_graph

from .conftest import upload_ready, wait_job


def test_metric_choice_graph_and_parsing(tmp_path: Path) -> None:
    assert (
        metric({"libvmaf", "ssim"}) == "vmaf"
        and metric({"ssim"}) == "ssim"
        and metric(None) == "ssim"
    )
    source = MediaInfo(width=1920, height=1080, fps=50, video_index=0)
    encoded = MediaInfo(width=1280, height=720, fps=25, video_index=0)
    graph = quality_graph(source, encoded, "vmaf", "vmaf.json")
    assert (
        "fps=25.000000,scale=1280:720" in graph
        and "[encoded][source]libvmaf=n_subsample=5" in graph
    )
    assert quality_graph(source, encoded, "ssim", "x").endswith("[encoded][source]ssim")
    log = tmp_path / "vmaf.json"
    log.write_text(json.dumps({"pooled_metrics": {"vmaf": {"mean": 93.4567}}}))
    assert parse_quality("vmaf", "", log) == 93.46
    assert parse_quality("vmaf", "VMAF score: 88.1", tmp_path / "missing") == 88.1
    stderr = (
        "[Parsed_ssim_3] SSIM Y:0.966339 (14.7) U:0.97 (16.8) V:0.98 (19.2) All:0.972127 (15.5)"
    )
    assert parse_quality("ssim", stderr, log) == 0.9721
    assert parse_quality("ssim", "", log) is None


def test_compress_reports_quality_when_asked(client: TestClient, samples: dict[str, Path]) -> None:
    video = upload_ready(client, samples["a"])

    def compress(**extra: Any) -> dict[str, Any]:
        edit = {"op": "compress", "preset": "ultrafast", **extra}
        response = client.post(f"/api/videos/{video['id']}/edit", json={"edit": edit})
        job = wait_job(client, response.json()["id"])
        assert job["status"] == "succeeded", job["error"]
        return job

    job = compress(measure_quality=True, quality="low", resolution=360)
    quality = job["params"]["quality"]
    assert quality["metric"] in {"vmaf", "ssim"}
    assert 0.5 < quality["score"] <= (100 if quality["metric"] == "vmaf" else 1)
    assert "quality" not in compress()["params"]
