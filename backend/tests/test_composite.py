from __future__ import annotations

import asyncio
import math
import statistics
import struct
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from reelvault.media.composite import plan_composite
from reelvault.media.ffmpeg import ffmpeg_args, run_command
from reelvault.media.ops import CompositeParams
from reelvault.media.probe import probe

from .conftest import upload_ready, wait_job, wait_ready


def clip(
    path: Path,
    color: str,
    *,
    duration: float = 2,
    size: str = "160x120",
    frequency: int | None = None,
    fps: int = 10,
    filter: str = "",
) -> Path:
    args = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color={color}:s={size}:r={fps}:d={duration}" + filter,
    ]
    if frequency:
        args += ["-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration}"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if frequency:
        args += ["-c:a", "aac", "-shortest"]
    subprocess.run([*args, str(path)], check=True)
    return path


def color(path: Path, x: int, y: int, time: float = 0.5) -> tuple[float, float, float]:
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(time),
            "-i",
            str(path),
            "-vf",
            f"crop=2:2:{x}:{y}",
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ]
    )
    return tuple(statistics.mean(raw[i::3]) for i in range(3))


def assert_color(
    path: Path, x: int, y: int, expected: tuple[int, int, int], time: float = 0.5
) -> None:
    measured = color(path, x, y, time)
    assert max(abs(a - b) for a, b in zip(measured, expected, strict=True)) < 15, measured


def render(paths: list[Path], out: Path, **params) -> dict:
    async def run() -> dict:
        sources = [(path, await probe("ffprobe", str(path))) for path in paths]
        p = CompositeParams(
            video_ids=[str(i) for i in range(len(paths))],
            width=320,
            height=240,
            fps=10,
            crf=0,
            **params,
        )
        plan, actual = plan_composite(p, sources, out)
        for command in plan.commands:
            await run_command(ffmpeg_args("ffmpeg", command))
        return actual

    return asyncio.run(run())


@pytest.mark.parametrize("layout", ["horizontal", "vertical", "grid"])
def test_real_split_layout_cells_background_and_sizes(tmp_path: Path, layout: str) -> None:
    paths = [
        clip(tmp_path / f"{i}.mp4", c, size=s, fps=f)
        for i, (c, s, f) in enumerate(
            [("red", "160x120", 10), ("blue", "120x160", 15), ("lime", "160x90", 25)]
        )
    ]
    out = tmp_path / "out.mp4"
    actual = render(paths, out, layout=layout, fit="cover", background="#ffffff")
    expected = [(255, 0, 0), (0, 0, 255), (0, 255, 0)]
    for (x, y, w, h), rgb in zip(actual["cells"], expected, strict=True):
        assert_color(out, x + w // 2, y + h // 2, rgb)
    if layout == "grid":
        assert_color(out, 240, 180, (255, 255, 255))
    info = asyncio.run(probe("ffprobe", str(out)))
    assert (info.width, info.height, info.fps) == (320, 240, 10)
    assert info.duration == 2 and not info.has_audio


@pytest.mark.parametrize("opacity", [0, 0.5, 1])
def test_real_pip_position_scale_and_alpha(tmp_path: Path, opacity: float) -> None:
    paths = [clip(tmp_path / "red.mp4", "red"), clip(tmp_path / "blue.mp4", "blue")]
    out = tmp_path / "out.mp4"
    actual = render(
        paths, out, layout="pip", fit="cover", pip_scale=40, pip_x=25, pip_y=75, pip_opacity=opacity
    )
    x, y, w, h = actual["cells"][1]
    assert (x, y, w, h) == (48, 108, 128, 96)
    assert_color(out, 10, 10, (255, 0, 0))
    assert_color(out, x + w // 2, y + h // 2, (round(255 * (1 - opacity)), 0, round(255 * opacity)))
    assert_color(out, x + w + 10, y + h // 2, (255, 0, 0))


def test_contain_preserves_display_aspect_and_cover_fills(tmp_path: Path) -> None:
    paths = [
        clip(tmp_path / "sar.mp4", "red", filter=",setsar=2"),
        clip(tmp_path / "portrait.mp4", "blue", size="120x160"),
    ]
    out = tmp_path / "contain.mp4"
    render(paths, out, layout="horizontal", fit="contain", background="#00ff00")
    # SAR 2 gives display ratio 8:3: its 160px-wide cell content is 60px high.
    assert_color(out, 80, 20, (0, 255, 0))
    assert_color(out, 80, 120, (255, 0, 0))
    assert_color(out, 240, 10, (0, 255, 0))
    assert_color(out, 240, 120, (0, 0, 255))
    cover = tmp_path / "cover.mp4"
    render(paths, cover, layout="horizontal", fit="cover")
    assert_color(cover, 80, 20, (255, 0, 0))
    assert_color(cover, 240, 10, (0, 0, 255))


@pytest.mark.parametrize("duration_mode,expected", [("first", 1), ("shortest", 1), ("longest", 2)])
def test_duration_modes_and_last_frame_extension(
    tmp_path: Path, duration_mode: str, expected: int
) -> None:
    paths = [
        clip(
            tmp_path / "changing.mp4",
            "red",
            duration=1,
            filter=",drawbox=x=0:y=0:w=iw:h=ih:color=blue:t=fill:enable='gte(t,0.5)'",
        ),
        clip(tmp_path / "long.mp4", "lime", duration=2),
    ]
    out = tmp_path / "out.mp4"
    actual = render(paths, out, layout="horizontal", fit="cover", duration_mode=duration_mode)
    assert actual["duration"] == expected
    assert asyncio.run(probe("ffprobe", str(out))).duration == expected
    if expected == 2:
        assert_color(out, 80, 120, (0, 0, 255), time=1.5)
        assert_color(out, 240, 120, (0, 255, 0), time=1.5)


def audio(path: Path) -> list[float]:
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "8000",
            "-f",
            "f32le",
            "-",
        ]
    )
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def power(values: list[float], frequency: int, time: float = 0.4) -> float:
    values = values[round(time * 8000) : round((time + 0.2) * 8000)]
    return abs(
        sum(
            v
            * complex(
                math.cos(2 * math.pi * frequency * i / 8000),
                math.sin(2 * math.pi * frequency * i / 8000),
            )
            for i, v in enumerate(values)
        )
    ) / len(values)


@pytest.mark.parametrize("mode,source", [("source", 0), ("source", 1), ("mix", 0), ("none", 0)])
def test_audio_selection_mixing_and_silence(tmp_path: Path, mode: str, source: int) -> None:
    paths = [
        clip(tmp_path / "red.mp4", "red", frequency=440),
        clip(tmp_path / "blue.mp4", "blue", frequency=880),
    ]
    out = tmp_path / "out.mp4"
    actual = render(paths, out, layout="horizontal", audio_mode=mode, audio_source=source)
    info = asyncio.run(probe("ffprobe", str(out)))
    if mode == "none":
        assert not info.has_audio and actual["audio_sources"] == []
        return
    assert info.has_audio and info.channels == 2 and info.sample_rate == 48000
    values = audio(out)
    a, b = power(values, 440), power(values, 880)
    if mode == "mix":
        assert 0.025 < a < 0.04 and 0.025 < b < 0.04, (a, b)
    elif source == 0:
        assert a > 0.05 and b < 0.005, (a, b)
    else:
        assert b > 0.05 and a < 0.005, (a, b)


def test_api_sources_history_and_multi_source_guards(
    client: TestClient, samples: dict[str, Path]
) -> None:
    first, second = [upload_ready(client, samples[key]) for key in ["a", "portrait"]]
    ids = [first["id"], second["id"]]
    edit = {
        "op": "composite",
        "video_ids": ids,
        "layout": "horizontal",
        "width": 320,
        "height": 240,
        "fps": 10,
    }
    assert (
        client.post("/api/edit-presets", json={"name": "fixed sources", "edit": edit}).status_code
        == 422
    )
    assert (
        client.post("/api/jobs/batch", json={"video_ids": [first["id"]], "edit": edit}).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/videos/{first['id']}/edit", json={"edit": edit, "output": {"mode": "replace"}}
        ).status_code
        == 400
    )
    response = client.post(f"/api/videos/{first['id']}/edit", json={"edit": edit})
    assert response.status_code == 200, response.text
    job = wait_job(client, response.json()["id"])
    assert job["status"] == "succeeded", job
    assert job["video_ids"] == ids
    assert job["params"]["actual_composition"]["duration"] == 4
    result = wait_ready(client, job["result_video_id"])
    assert (result["width"], result["height"]) == (320, 240)
    history = client.get(f"/api/videos/{result['id']}/history").json()["nodes"][0]
    assert [s["id"] for s in history["sources"]] == ids and history["can_recreate"]
    replay = client.post(f"/api/videos/{result['id']}/recreate")
    assert replay.status_code == 200, replay.text
    assert wait_job(client, replay.json()["id"])["status"] == "succeeded"
    other = upload_ready(client, samples["b"])
    assert client.post(f"/api/videos/{other['id']}/edit", json={"edit": edit}).status_code == 400
    for params in [
        {"video_ids": ["same", "same"]},
        {"video_ids": ["a", "b", "c"]},
        {"width": 321},
        {"audio_source": 2},
        {"pip_opacity": float("nan")},
    ]:
        with pytest.raises(ValidationError):
            CompositeParams(**({"video_ids": ["a", "b"]} | params))


def test_nine_input_grid_exact_canvas_and_all_cells(tmp_path: Path) -> None:
    colors = ["red", "blue", "lime", "yellow", "cyan", "magenta", "white", "black", "red"]
    paths = [clip(tmp_path / f"{i}.mp4", c, duration=1) for i, c in enumerate(colors)]
    out = tmp_path / "nine.mp4"
    actual = render(paths, out, layout="grid", columns=3, fit="cover")
    expected = [
        (255, 0, 0),
        (0, 0, 255),
        (0, 255, 0),
        (255, 255, 0),
        (0, 255, 255),
        (255, 0, 255),
        (255, 255, 255),
        (0, 0, 0),
        (255, 0, 0),
    ]
    for (x, y, w, h), rgb in zip(actual["cells"], expected, strict=True):
        assert_color(out, x + w // 2, y + h // 2, rgb)
    info = asyncio.run(probe("ffprobe", str(out)))
    assert (info.width, info.height) == (320, 240)


def test_delayed_audio_silent_source_and_short_audio_padding(tmp_path: Path) -> None:
    first = clip(tmp_path / "first.mp4", "red", duration=2)
    delayed = tmp_path / "delayed.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=blue:s=160x120:r=10:d=2",
            "-itsoffset",
            "0.6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.8",
            "-c:v",
            "ffv1",
            "-c:a",
            "pcm_s16le",
            str(delayed),
        ],
        check=True,
    )
    out = tmp_path / "mixed.mp4"
    actual = render([first, delayed], out, layout="pip", audio_mode="mix")
    assert actual["audio_sources"] == [1]
    values = audio(out)
    assert power(values, 440, 0.1) < 0.001
    assert power(values, 440, 0.8) > 0.05
    assert power(values, 440, 1.7) < 0.001
    silent = tmp_path / "silent.mp4"
    render([first, delayed], silent, layout="horizontal", audio_mode="source", audio_source=0)
    assert not asyncio.run(probe("ffprobe", str(silent))).has_audio


def test_delayed_video_uses_leading_frame_without_shifting_audio(tmp_path: Path) -> None:
    delayed = tmp_path / "delayed-video.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-itsoffset",
            "0.6",
            "-f",
            "lavfi",
            "-i",
            "color=red:s=160x120:r=10:d=1.4,drawbox=x=0:y=0:w=iw:h=ih:color=blue:t=fill:enable='gte(t,0.4)'",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "ffv1",
            "-c:a",
            "pcm_s16le",
            str(delayed),
        ],
        check=True,
    )
    other = clip(tmp_path / "other.mp4", "lime", duration=2)
    out = tmp_path / "out.mp4"
    render([delayed, other], out, layout="horizontal", fit="cover")
    assert_color(out, 80, 120, (255, 0, 0), time=0.5)
    assert_color(out, 80, 120, (0, 0, 255), time=1.3)
    assert power(audio(out), 440, 0.2) > 0.05
