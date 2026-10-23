from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from reelvault.media import derive

from .conftest import upload_ready, wait_job


def test_parse_peaks_converts_dbfs() -> None:
    text = "frame:0\nlavfi.astats.Overall.Peak_level=-6.020600\nframe:1\n"
    text += "lavfi.astats.Overall.Peak_level=-inf\n"
    assert derive.parse_peaks(text) == [0.5, 0.0]
    assert derive.parse_peaks("lavfi.astats.Overall.Peak_level=0.5\n") == [1.0]


def test_waveform_follows_loud_and_silent_audio(
    client: TestClient, tmp_path: Path, samples: dict[str, Path]
) -> None:
    source = tmp_path / "gap.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=160x120:d=4"]
        + ["-f", "lavfi", "-i", "sine=f=440:d=4,volume='if(between(t,1,3),0,1)':eval=frame"]
        + ["-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)],
        check=True,
    )
    video = upload_ready(client, source)
    assert video["waveform_url"].startswith(f"/api/videos/{video['id']}/waveform.json?v=")
    data = client.get(video["waveform_url"]).json()
    peaks = data["peaks"]
    assert 900 <= len(peaks) <= 1020 and abs(data["duration"] - video["duration"]) < 0.01
    loud, silent = peaks[: len(peaks) // 5], peaks[len(peaks) * 2 // 5 : len(peaks) * 3 // 5]
    assert min(loud[5:]) > 0.1 and max(silent) < 0.01  # the sine peaks at 1/8
    # Regenerating thumbnails rebuilds the waveform under a new URL.
    job = client.post(f"/api/videos/{video['id']}/sprite").json()
    assert wait_job(client, job["id"])["status"] == "succeeded"
    assert client.get(f"/api/videos/{video['id']}").json()["waveform_url"] != video["waveform_url"]
    silent_video = upload_ready(client, samples["portrait"])
    assert silent_video["waveform_url"] is None
