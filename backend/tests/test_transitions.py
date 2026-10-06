from __future__ import annotations

import asyncio
import math
import struct
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reelvault.media.ffmpeg import ffmpeg_args, run_command
from reelvault.media.ops import MergeParams, OpError, plan_merge
from reelvault.media.probe import probe

from .conftest import upload_ready, wait_job, wait_ready


def make_clip(
    path: Path, color: str, frequency: int | None, size: str = "160x120", fps: int = 25
) -> Path:
    args = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color={color}:s={size}:r={fps}:d=2"]
    if frequency:
        args += ["-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=2"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if frequency:
        args += ["-c:a", "aac", "-shortest"]
    subprocess.run([*args, str(path)], check=True)
    return path


def color_at(path: Path, time: float) -> tuple[float, float, float]:
    data = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(time),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "crop=16:16:40:40",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
    )
    return tuple(sum(data[i::3]) / (len(data) / 3) for i in range(3))  # type: ignore[return-value]


def spectral_amplitude(path: Path, start: float, frequency: int) -> float:
    data = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(start),
            "-i",
            str(path),
            "-t",
            "0.1",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "f32le",
            "pipe:1",
        ]
    )
    samples = struct.unpack("<" + "f" * (len(data) // 4), data)
    real = sum(v * math.cos(2 * math.pi * frequency * i / 48000) for i, v in enumerate(samples))
    imag = sum(v * math.sin(2 * math.pi * frequency * i / 48000) for i, v in enumerate(samples))
    return 2 * math.hypot(real, imag) / len(samples)


@pytest.mark.parametrize(
    "transition",
    [
        "fade",
        "fadeblack",
        "fadewhite",
        "wipeleft",
        "wiperight",
        "slideleft",
        "slideright",
        "dissolve",
        "circleopen",
    ],
)
def test_real_transition_pixels_audio_blend_and_duration(tmp_path: Path, transition: str) -> None:
    a = make_clip(tmp_path / "red.mp4", "red", 440)
    b = make_clip(tmp_path / "blue.mp4", "blue", 880, fps=30)
    sources = [(p, asyncio.run(probe("ffprobe", str(p)))) for p in [a, b]]
    params = MergeParams(video_ids=["a", "b"], transition=transition, transition_duration=0.5)
    out = tmp_path / "merged.mp4"
    plan = plan_merge(params, sources, out, tmp_path)
    for cmd in plan.commands:
        asyncio.run(run_command(ffmpeg_args("ffmpeg", cmd)))
    info = asyncio.run(probe("ffprobe", str(out)))
    assert abs(info.duration - 3.5) < 0.08
    assert info.width == 160 and info.height == 120 and info.audio_codec == "aac"
    r, _, b_value = color_at(out, 0.5)
    assert r > 240 and b_value < 10
    r, _, b_value = color_at(out, 2.5)
    assert b_value > 240 and r < 10
    if transition == "fade":
        r, _, b_value = color_at(out, 1.75)
        assert 90 < r < 165 and 90 < b_value < 165
    first = spectral_amplitude(out, 0.5, 440)
    second = spectral_amplitude(out, 2.5, 880)
    assert spectral_amplitude(out, 0.5, 880) < first * 0.05
    assert spectral_amplitude(out, 2.5, 440) < second * 0.05
    assert 0.25 < spectral_amplitude(out, 1.7, 440) / first < 0.75
    assert 0.25 < spectral_amplitude(out, 1.7, 880) / second < 0.75


def test_three_sources_missing_audio_resize_timing_and_validation(tmp_path: Path) -> None:
    paths = [
        make_clip(tmp_path / "a.mp4", "red", 440),
        make_clip(tmp_path / "b.mp4", "green", None, size="120x160", fps=30),
        make_clip(tmp_path / "c.mp4", "blue", 880),
    ]
    sources = [(p, asyncio.run(probe("ffprobe", str(p)))) for p in paths]
    p = MergeParams(video_ids=["a", "b", "c"], transition="fade", transition_duration=0.5)
    out = tmp_path / "merged.mp4"
    plan = plan_merge(p, sources, out, tmp_path)
    for cmd in plan.commands:
        asyncio.run(run_command(ffmpeg_args("ffmpeg", cmd)))
    info = asyncio.run(probe("ffprobe", str(out)))
    assert abs(info.duration - 5) < 0.08 and info.width == 160 and info.height == 120
    assert spectral_amplitude(out, 2.2, 440) < 0.001
    assert spectral_amplitude(out, 2.2, 880) < 0.001
    assert color_at(out, 4.5)[2] > 240
    with pytest.raises(OpError, match="中间片段"):
        plan_merge(p.model_copy(update={"transition_duration": 1.1}), sources, out, tmp_path)
    with pytest.raises(ValueError):
        MergeParams(video_ids=["a", "b"], mode="lossless", transition="fade")


def test_transition_api_history_and_replay(client: TestClient, samples: dict[str, Path]) -> None:
    a = upload_ready(client, samples["a"])
    b = upload_ready(client, samples["portrait"])
    edit = {
        "op": "merge",
        "video_ids": [a["id"], b["id"]],
        "transition": "fade",
        "transition_duration": 0.5,
    }
    r = client.post(f"/api/videos/{a['id']}/edit", json={"edit": edit})
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job
    output = wait_ready(client, job["result_video_id"])
    assert abs(output["duration"] - 6.5) < 0.1
    node = client.get(f"/api/videos/{output['id']}/history").json()["nodes"][0]
    assert node["edit"]["transition"] == "fade" and node["can_recreate"]
    replay = client.post(f"/api/videos/{output['id']}/recreate")
    assert replay.status_code == 200
    assert wait_job(client, replay.json()["id"])["status"] == "succeeded"


def test_silent_merge_and_original_audio_delay(tmp_path: Path) -> None:
    a = make_clip(tmp_path / "red.mp4", "red", None)
    b = make_clip(tmp_path / "blue.mp4", "blue", None)
    params = MergeParams(video_ids=["a", "b"], transition="fade", transition_duration=0.5)
    out = tmp_path / "silent.mp4"
    sources = [(p, asyncio.run(probe("ffprobe", str(p)))) for p in [a, b]]
    for cmd in plan_merge(params, sources, out, tmp_path).commands:
        asyncio.run(run_command(ffmpeg_args("ffmpeg", cmd)))
    info = asyncio.run(probe("ffprobe", str(out)))
    assert not info.has_audio and abs(info.duration - 3.5) < 0.1
    delayed = tmp_path / "delayed.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(a),
            "-itsoffset",
            "0.6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.4",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-t",
            "2",
            str(delayed),
        ],
        check=True,
    )
    sources[0] = (delayed, asyncio.run(probe("ffprobe", str(delayed))))
    out = tmp_path / "delayed-merge.mp4"
    for cmd in plan_merge(params, sources, out, tmp_path).commands:
        asyncio.run(run_command(ffmpeg_args("ffmpeg", cmd)))
    assert spectral_amplitude(out, 0.2, 440) < 0.001
    assert spectral_amplitude(out, 0.8, 440) > 0.1
    assert spectral_amplitude(out, 2.5, 440) < 0.001


def test_non_square_pixels_keep_display_shape(tmp_path: Path) -> None:
    original = make_clip(tmp_path / "original.mp4", "red", None)
    wide = tmp_path / "wide.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(original),
            "-vf",
            "setsar=2",
            "-c:v",
            "libx264",
            str(wide),
        ],
        check=True,
    )
    blue = make_clip(tmp_path / "blue.mp4", "blue", None)
    sources = [(p, asyncio.run(probe("ffprobe", str(p)))) for p in [wide, blue]]
    out = tmp_path / "output.mp4"
    params = MergeParams(video_ids=["a", "b"], transition="fade")
    for cmd in plan_merge(params, sources, out, tmp_path).commands:
        asyncio.run(run_command(ffmpeg_args("ffmpeg", cmd)))
    assert color_at(out, 0.5)[0] > 240
    top = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            "0.5",
            "-i",
            str(out),
            "-frames:v",
            "1",
            "-vf",
            "crop=16:16:40:8",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
    )
    # 2:1 sample aspect ratio makes the 160×120 source display as 320×120.
    # Fitting it into the target produces a 160×60 image and black top/bottom bars.
    assert max(top) < 5
