from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from reelvault.config import Settings
from reelvault.media import derive

from .conftest import upload_ready


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
