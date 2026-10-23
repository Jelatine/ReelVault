from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reelvault.config import Settings
from reelvault.media import ops
from reelvault.media.probe import MediaInfo, parse_probe
from reelvault.media.transitions import plan_transitions
from reelvault.models import Video

from .conftest import upload_ready, wait_job, wait_ready


@pytest.fixture(scope="module")
def vfr(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """30 fps for 2 s, then 12 fps; the video track ends before the audio."""
    path = tmp_path_factory.mktemp("vfr") / "vfr.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=30:duration=6",
        ]
        + ["-f", "lavfi", "-i", "sine=duration=6"]
        + ["-vf", "setpts='if(lt(N,60),N/30,2+(N-60)/12)/TB'", "-fps_mode", "passthrough"]
        + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )
    return path


def streams(path: Path) -> dict[str, dict[str, str]]:
    data = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)]
        )
    )
    return {s["codec_type"]: s for s in data["streams"]}


def test_probe_detects_variable_frame_rate(vfr: Path) -> None:
    data = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(vfr)]
        )
    )
    info = parse_probe(data, str(vfr))
    assert info.variable_frame_rate and info.nominal_fps == 30 and info.fps < 20
    assert info.output_fps == 30 and info.to_meta()["variable_frame_rate"] is True
    timebase = MediaInfo(fps=29.5, nominal_fps=90000)
    assert timebase.variable_frame_rate and timebase.output_fps == 29.97
    assert not MediaInfo(fps=29.97, nominal_fps=29.97).variable_frame_rate


def test_transitions_and_effects_use_the_nominal_rate(tmp_path: Path) -> None:
    vfr = MediaInfo(duration=6, fps=18.5, nominal_fps=30, width=320, height=240)
    cfr = MediaInfo(duration=3, fps=25, nominal_fps=25, width=320, height=240)
    params = ops.MergeParams(video_ids=["a", "b"], transition="fade")
    command = plan_transitions(
        params, [(tmp_path / "a", vfr), (tmp_path / "b", cfr)], tmp_path / "o.mp4"
    ).commands[0]
    assert re.search(r"fps=30(\.0)?,", command[command.index("-filter_complex") + 1])
    assert command[command.index("-fps_mode") + 1] == "cfr"


def test_merge_keeps_every_source_in_sync_with_vfr(
    client: TestClient, settings: Settings, vfr: Path, samples: dict[str, Path]
) -> None:
    clip = upload_ready(client, samples["b"])  # 3 s at 25 fps
    variable = upload_ready(client, vfr)
    assert variable["media"]["variable_frame_rate"] is True
    assert variable["media"]["nominal_fps"] == 30
    ids = [clip["id"], variable["id"], clip["id"]]
    response = client.post(
        f"/api/videos/{clip['id']}/edit",
        json={"edit": {"op": "merge", "video_ids": ids, "mode": "reencode"}},
    )
    job = wait_job(client, response.json()["id"])
    assert job["status"] == "succeeded", job["error"]
    wait_ready(client, job["result_video_id"])
    with client.app.state.sessionmaker() as db:
        out = settings.data_dir / db.get(Video, job["result_video_id"]).file_path
    tracks = streams(out)
    video, audio = tracks["video"], tracks["audio"]
    assert video["r_frame_rate"] == video["avg_frame_rate"] == "25/1"
    # The VFR source's short video track is padded, so the last clip stays in sync.
    assert float(video["duration"]) == pytest.approx(12, abs=0.05)
    assert float(audio["duration"]) == pytest.approx(12, abs=0.05)
    assert int(video["nb_frames"]) == 300
