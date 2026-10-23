from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reelvault.media.detect import detect_crop, detect_silence, keep_segments
from reelvault.media.probe import probe

from .conftest import upload_ready


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def letterboxed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """320x180 picture with 30-pixel black bars top and bottom (320x240 frame)."""
    out = tmp_path_factory.mktemp("letterbox") / "letterboxed.mp4"
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=4",
        "-vf", "pad=320:240:0:30:black", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
    )  # fmt: skip
    return out


@pytest.fixture(scope="module")
def gapped(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Tone 0-1 s, silence 1-3 s, tone 3-4 s, silence 4-6 s."""
    out = tmp_path_factory.mktemp("gapped") / "gapped.mp4"
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-af", "volume='if(between(t,1,3)+gte(t,4),0,1)':eval=frame",
        "-c:v", "libx264", "-c:a", "aac", "-shortest", str(out),
    )  # fmt: skip
    return out


def test_detect_crop_finds_letterbox_and_ignores_full_frames(
    letterboxed: Path, samples: dict[str, Path]
) -> None:
    info = asyncio.run(probe("ffprobe", str(letterboxed)))
    assert asyncio.run(detect_crop("ffmpeg", letterboxed, info)) == {
        "x": 0,
        "y": 30,
        "width": 320,
        "height": 180,
    }
    full = asyncio.run(probe("ffprobe", str(samples["a"])))
    assert asyncio.run(detect_crop("ffmpeg", samples["a"], full)) is None


def test_detect_silence_and_keep_segments(gapped: Path) -> None:
    info = asyncio.run(probe("ffprobe", str(gapped)))
    silences = asyncio.run(detect_silence("ffmpeg", gapped, info, noise_db=-50, min_duration=0.5))
    assert len(silences) == 2
    (s1, e1), (s2, e2) = silences
    assert s1 == pytest.approx(1, abs=0.1) and e1 == pytest.approx(3, abs=0.1)
    # Trailing silence runs to the end of the file.
    assert s2 == pytest.approx(4, abs=0.1) and e2 == pytest.approx(info.duration, abs=0.01)
    segments = keep_segments(silences, info.duration, padding=0.1)
    assert len(segments) == 2
    assert segments[0]["start"] == 0 and segments[0]["end"] == pytest.approx(1.1, abs=0.1)
    assert segments[1]["start"] == pytest.approx(2.9, abs=0.1)
    assert segments[1]["end"] == pytest.approx(4.1, abs=0.1)


def test_keep_segments_skips_short_gaps() -> None:
    assert keep_segments([(1, 1.2)], 5, padding=0.1) == [{"start": 0, "end": 5}]
    assert keep_segments([(0, 2)], 5, padding=0) == [{"start": 2, "end": 5}]
    assert keep_segments([(0, 5)], 5, padding=0) == []
    # Leading and trailing silence is removed entirely, padding only inner edges.
    assert keep_segments([(0, 2), (4.5, 5)], 5, padding=0.2) == [{"start": 1.8, "end": 4.7}]


def test_analysis_endpoints(
    client: TestClient, letterboxed: Path, gapped: Path, samples: dict[str, Path]
) -> None:
    video = upload_ready(client, letterboxed)
    response = client.get(f"/api/videos/{video['id']}/crop-detect")
    assert response.status_code == 200, response.text
    assert response.json()["crop"] == {"x": 0, "y": 30, "width": 320, "height": 180}

    video = upload_ready(client, gapped)
    response = client.get(
        f"/api/videos/{video['id']}/silence", params={"noise_db": -50, "min_duration": 0.5}
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["segments"]) == 2

    silent = upload_ready(client, samples["portrait"])
    response = client.get(f"/api/videos/{silent['id']}/silence")
    assert response.status_code == 400 and response.json()["code"] == "no_audio_track"
    bad = client.get(f"/api/videos/{video['id']}/silence", params={"noise_db": 0})
    assert bad.status_code == 422
