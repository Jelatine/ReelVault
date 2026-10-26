from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reelvault.config import Settings
from reelvault.media import chapters, derive, ops

from .conftest import upload_ready, wait_job, wait_ready


def luma(path: Path) -> float:
    out = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(path), "-vf", "signalstats,metadata=mode=print"]
        + ["-f", "null", "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stderr
    return float(next(line for line in out.splitlines() if "YAVG=" in line).split("=")[-1])


def test_parse_and_pick_skip_black_and_flat_frames() -> None:
    text = "".join(
        f"frame:{i}    pts:{i}       pts_time:{i / 4}\n"
        f"lavfi.signalstats.YLOW={low}\nlavfi.signalstats.YAVG={avg}\nlavfi.signalstats.YHIGH={high}\n"
        for i, (low, avg, high) in enumerate(
            [(16, 16, 16), (120, 128, 130), (30, 90, 200), (20, 60, 230)]
        )
    )
    samples = derive.parse_cover_stats(text, 10)
    assert samples[0] == (10, 16, 0) and len(samples) == 4
    assert derive.pick_cover(samples) == 10.75  # most detail; flat grey and black skipped
    assert derive.pick_cover(samples[:2]) is None


def test_automatic_cover_skips_a_black_intro(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    source = tmp_path / "intro.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=320x240:d=3"]
        + ["-f", "lavfi", "-i", "testsrc=s=320x240:d=5"]
        + [
            "-filter_complex",
            "[0][1]concat=n=2:v=1:a=0,format=yuv420p",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
    )
    video = upload_ready(client, source)
    # The default (10%, 0.8 s) is black; the cover comes from the first picture.
    assert 3 <= video["media"]["auto_cover_time"] < 3.8
    assert luma(settings.derived_dir / video["id"] / derive.POSTER) > 60


def test_cover_times_follow_the_edit_timeline() -> None:
    trim = ops.TrimParams(segments=[ops.Segment(start=1, end=2), ops.Segment(start=3, end=5)])
    assert chapters.map_time(trim, 3.5, [6]) == pytest.approx(1.5)
    assert chapters.map_time(trim, 2.5, [6]) is None  # that moment was cut out
    assert chapters.map_time(ops.SpeedParams(factor=2), 4, [6]) == pytest.approx(2)
    assert chapters.map_time(ops.RotateParams(angle=90), 4, [6]) == 4
    merge = ops.MergeParams(video_ids=["a", "b"])
    assert chapters.map_time(merge, 1, [3, 2]) == 1


def edit_result(client: TestClient, video_id: str, edit: dict, output: dict | None = None) -> dict:
    r = client.post(f"/api/videos/{video_id}/edit", json={"edit": edit, "output": output or {}})
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job["error"]
    return wait_ready(client, job["result_video_id"])


def test_edits_keep_an_uploaded_cover_image(
    client: TestClient, settings: Settings, samples: dict[str, Path], tmp_path: Path
) -> None:
    video = upload_ready(client, samples["a"])
    image = tmp_path / "cover.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=64x48", "-frames:v", "1"]
        + [str(image)],
        check=True,
    )
    r = client.post(f"/api/videos/{video['id']}/cover/upload", files={"file": image.open("rb")})
    assert r.status_code == 200, r.text
    edited = edit_result(client, video["id"], {"op": "trim", "segments": [{"start": 1, "end": 3}]})
    poster = (settings.derived_dir / edited["id"] / derive.POSTER).read_bytes()
    assert poster == (settings.derived_dir / video["id"] / derive.POSTER).read_bytes()
    assert edited["media"]["custom_cover"] is True


def test_edits_keep_a_chosen_cover_frame(client: TestClient, samples: dict[str, Path]) -> None:
    video = upload_ready(client, samples["a"])
    assert client.post(f"/api/videos/{video['id']}/cover", json={"time": 3}).status_code == 200
    trim = {"op": "trim", "segments": [{"start": 1, "end": 4}]}
    assert edit_result(client, video["id"], trim)["cover_time"] == pytest.approx(2)
    # The frame is gone after this cut, so the new video picks its own cover.
    cut = {"op": "trim", "segments": [{"start": 0, "end": 2}]}
    assert edit_result(client, video["id"], cut)["cover_time"] is None
    replaced = edit_result(client, video["id"], trim, {"mode": "replace"})
    assert replaced["id"] == video["id"] and replaced["cover_time"] == pytest.approx(2)
