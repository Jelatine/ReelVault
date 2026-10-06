import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from reelvault.config import Settings
from reelvault.media import timing

from .conftest import make_video, upload_ready
from .test_edit import result_video, run_edit


def test_parse_variable_frames_and_stream_start() -> None:
    data = {
        "format": {"start_time": "5"},
        "frames": [
            {"best_effort_timestamp_time": "5.0", "key_frame": 1},
            {"best_effort_timestamp_time": "5.04", "key_frame": 0},
            {"best_effort_timestamp_time": "5.13", "key_frame": 1},
            {"best_effort_timestamp_time": "5.04", "key_frame": 0},
            {"best_effort_timestamp_time": "4.0", "key_frame": 1},
        ],
    }
    assert timing.parse_timing(data) == {"frames": [0, 0.04, 0.13], "keyframes": [0, 0.13]}
    assert timing.snap_cut(0.1, 0.5, [0, 0.13, 0.8], 1) == (0, 0.8)
    assert timing.snap_cut(0.13, 0.8, [0, 0.13, 0.8], 1) == (0.13, 0.8)
    assert timing.snap_cut(0.8, 1, [0, 0.13, 0.8], 1) == (0.8, 1)


def test_timing_cache_invalidates_on_source_change(
    client: TestClient,
    settings: Settings,
    samples: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = upload_ready(client, samples["a"])
    url = f"/api/videos/{video['id']}/timing"
    first = client.get(url)
    assert first.status_code == 200, first.text
    frames = first.json()
    assert len(frames["frames"]) == 100
    assert frames["frames"][:3] == [0, 0.04, 0.08]
    assert frames["keyframes"] == [0, 1, 2, 3]
    assert (settings.derived_dir / video["id"] / "timing.json").exists()
    calls = []
    original = timing.run_command

    async def spy(args: list[str], **kwargs: Any):
        calls.append(args)
        return await original(args, **kwargs)

    monkeypatch.setattr(timing, "run_command", spy)
    assert client.get(url).json() == frames
    assert (
        client.get(url, params={"keyframes_only": True}).json()["keyframes"] == frames["keyframes"]
    )
    assert calls == []
    source = next(settings.library_dir.glob(f"{video['id']}.*"))
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert client.get(url).json() == frames
    assert len(calls) == 1
    client.delete(f"/api/videos/{video['id']}")
    assert client.get(url).status_code == 404


def test_actual_vfr_timestamps(client: TestClient, tmp_path: Path) -> None:
    sample = make_video(
        tmp_path / "vfr.mp4",
        duration=1,
        audio=False,
        extra=["-vf", "select='not(mod(n,3))+not(mod(n,5))'", "-fps_mode", "vfr"],
    )
    video = upload_ready(client, sample)
    result = client.get(f"/api/videos/{video['id']}/timing").json()
    differences = {
        round(b - a, 6) for a, b in zip(result["frames"], result["frames"][1:], strict=False)
    }
    assert len(differences) > 1
    assert result["frames"][:3] == [0, 0.12, 0.2]


def test_fast_trim_records_and_uses_actual_keyframe_cuts(
    client: TestClient, samples: dict[str, Path]
) -> None:
    video = upload_ready(client, samples["a"])
    job = run_edit(
        client,
        video["id"],
        {"op": "trim", "mode": "fast", "segments": [{"start": 1.3, "end": 2.2}]},
    )
    assert job["params"]["requested_edit"]["segments"] == [{"start": 1.3, "end": 2.2}]
    assert job["params"]["edit"]["segments"] == [{"start": 1, "end": 3}]
    output = result_video(client, job)
    assert 1.9 < output["duration"] < 2.3
