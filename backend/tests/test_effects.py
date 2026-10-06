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

from reelvault.config import Settings
from reelvault.library import abs_path
from reelvault.media import effects
from reelvault.media.effects import plan_effect
from reelvault.media.ffmpeg import ffmpeg_args, run_command
from reelvault.media.ops import EffectParams, OpError
from reelvault.media.probe import probe
from reelvault.models import Video

from .conftest import upload_ready, wait_job


def ramp(path: Path, *, fps: str = "10", count: int = 40, audio: bool = True) -> Path:
    rate = float(fps) if "/" not in fps else 30000 / 1001
    raw = b"".join(bytes([40 + n * 2]) * (64 * 48) for n in range(count))
    args = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pixel_format",
        "gray",
        "-video_size",
        "64x48",
        "-framerate",
        fps,
        "-i",
        "pipe:0",
    ]
    if audio:
        args += [
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc=0.4*sin(2*PI*(220+220*floor(t))*t):s=48000:d={count / rate}",
        ]
    args += ["-c:v", "ffv1", "-pix_fmt", "yuv420p", "-g", "1"]
    if audio:
        args += ["-c:a", "pcm_s16le", "-shortest"]
    subprocess.run([*args, str(path)], input=raw, check=True)
    return path


def frame_values(path: Path) -> list[float]:
    raw = subprocess.check_output(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    )
    area = 64 * 48
    return [statistics.mean(raw[i : i + area]) for i in range(0, len(raw), area)]


def assert_frames(path: Path, original: list[float], indices: list[int]) -> None:
    values = frame_values(path)
    assert len(values) == len(indices), (len(values), len(indices))
    assert (
        max(abs(value - original[index]) for value, index in zip(values, indices, strict=True)) <= 1
    )


def audio_values(path: Path) -> list[float]:
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ar",
            "8000",
            "-ac",
            "1",
            "-f",
            "f32le",
            "-",
        ]
    )
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def power(samples: list[float], position: float, freq: int) -> float:
    clip = samples[round(position * 8000) : round((position + 0.1) * 8000)]
    return abs(
        sum(
            value
            * complex(
                math.cos(2 * math.pi * freq * n / 8000), math.sin(2 * math.pi * freq * n / 8000)
            )
            for n, value in enumerate(clip)
        )
    ) / len(clip)


def render(src: Path, tmp: Path, params: EffectParams) -> tuple[Path, dict]:
    tmp.mkdir()
    out = tmp / "out.mp4"

    async def run() -> dict:
        info = await probe("ffprobe", str(src))
        plan, actual = plan_effect(params, (src, info), out, tmp)
        for command in plan.commands:
            await run_command(ffmpeg_args("ffmpeg", command), cwd=plan.cwd)
        return actual

    return out, asyncio.run(run())


def test_reverse_multichunk_preserves_surroundings_and_reverses_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = ramp(tmp_path / "ramp.mkv")
    monkeypatch.setattr(effects, "REVERSE_BUFFER_BYTES", (64 * 48 * 1.5 + 192000 / 10) * 4)
    out, actual = render(src, tmp_path / "reverse", EffectParams(start=1, end=3, crf=0))
    assert len(list(out.parent.glob("piece-*.mkv"))) == 7
    assert actual["output_duration"] == 4
    assert_frames(
        out, frame_values(src), list(range(10)) + list(range(29, 9, -1)) + list(range(30, 40))
    )
    samples = audio_values(out)
    for position, frequency in [(0.25, 220), (1.25, 660), (2.25, 440), (3.25, 880)]:
        assert power(samples, position, frequency) > 0.15
    assert power(samples, 1.25, 440) < 0.02


def test_freeze_inserts_identical_frames_and_silence(tmp_path: Path) -> None:
    src = ramp(tmp_path / "ramp.mkv")
    out, actual = render(
        src, tmp_path / "freeze", EffectParams(mode="freeze", start=1, duration=1.5, crf=0)
    )
    assert actual["output_duration"] == 5.5
    assert_frames(out, frame_values(src), list(range(10)) + [10] * 15 + list(range(10, 40)))
    samples = audio_values(out)
    assert power(samples, 0.25, 220) > 0.15
    assert power(samples, 2.75, 440) > 0.15
    assert power(samples, 4.75, 880) > 0.15
    assert statistics.mean(v * v for v in samples[round(1.2 * 8000) : round(2.3 * 8000)]) < 1e-6


def test_slow_local_interval_preserves_pitch_and_returns_to_normal(tmp_path: Path) -> None:
    src = ramp(tmp_path / "ramp.mkv")
    out, actual = render(
        src, tmp_path / "slow", EffectParams(mode="slow", start=1, end=3, factor=0.5, crf=0)
    )
    assert actual["output_duration"] == 6
    assert_frames(
        out,
        frame_values(src),
        list(range(10)) + [n for n in range(10, 30) for _ in range(2)] + list(range(30, 40)),
    )
    samples = audio_values(out)
    for position, frequency in [
        (0.25, 220),
        (1.25, 440),
        (2.25, 440),
        (3.25, 660),
        (4.25, 660),
        (5.25, 880),
    ]:
        assert power(samples, position, frequency) > 0.14
    assert power(samples, 1.25, 220) < 0.02


@pytest.mark.parametrize("mode", ["reverse", "freeze", "slow"])
def test_fractional_rate_and_silent_sources(tmp_path: Path, mode: str) -> None:
    src = ramp(tmp_path / "fraction.mkv", fps="30000/1001", count=90, audio=False)
    out, actual = render(
        src,
        tmp_path / mode,
        EffectParams(mode=mode, start=0.4, end=1.8, duration=0.7, factor=0.5, crf=0),
    )
    first, last = round(0.4 * 30000 / 1001), round(1.8 * 30000 / 1001)
    if mode == "reverse":
        indices = list(range(first)) + list(range(last - 1, first - 1, -1)) + list(range(last, 90))
    elif mode == "freeze":
        indices = list(range(first)) + [first] * round(0.7 * 30000 / 1001) + list(range(first, 90))
    else:
        indices = (
            list(range(first))
            + [n for n in range(first, last) for _ in range(2)]
            + list(range(last, 90))
        )
    assert_frames(out, frame_values(src), indices)
    info = asyncio.run(probe("ffprobe", str(out)))
    assert not info.has_audio
    assert abs(info.duration - actual["output_duration"]) < 0.01


def test_effect_validation_clamp_and_frame_alignment(tmp_path: Path) -> None:
    for params in [
        {"end": 0},
        {"start": 2, "end": 1},
        {"mode": "slow", "end": 1, "factor": 1},
        {"mode": "freeze", "duration": 0},
        {"end": float("nan")},
    ]:
        with pytest.raises(ValidationError):
            EffectParams(**params)
    src = ramp(tmp_path / "ramp.mkv")
    info = asyncio.run(probe("ffprobe", str(src)))
    for params in [EffectParams(start=4, end=5), EffectParams(start=1, end=1.01)]:
        with pytest.raises(OpError):
            plan_effect(params, (src, info), tmp_path / "out.mp4", tmp_path)
    _, actual = plan_effect(
        EffectParams(start=0.12, end=10), (src, info), tmp_path / "out.mp4", tmp_path
    )
    assert actual["start"] == 0.1 and actual["end"] == 4


def test_presets_batch_history_replace_and_cleanup(
    client: TestClient,
    samples: dict[str, Path],
    settings: Settings,
) -> None:
    video = upload_ready(client, samples["a"])
    edit = {"op": "effect", "mode": "freeze", "start": 1, "duration": 0.5}
    preset = client.post("/api/edit-presets", json={"name": "插入定格", "edit": edit})
    assert preset.status_code == 200, preset.text
    batch = client.post(
        "/api/jobs/batch", json={"video_ids": [video["id"]], "preset_id": preset.json()["id"]}
    )
    assert batch.status_code == 200, batch.text
    job = wait_job(client, batch.json()[0]["id"])
    assert job["status"] == "succeeded", job
    assert abs(job["params"]["actual_effect"]["output_duration"] - 4.5) <= 0.04
    assert job["params"]["encoding"]["encoder"] == "libx264"
    assert not (settings.tmp_dir / f"job-{job['id']}").exists()
    result = job["result_video_id"]
    history = client.get(f"/api/videos/{result}/history").json()
    assert history["nodes"][0]["can_recreate"]
    recreated = client.post(f"/api/videos/{result}/recreate")
    assert recreated.status_code == 200, recreated.text
    assert wait_job(client, recreated.json()["id"])["status"] == "succeeded"
    replaced = client.post(
        f"/api/videos/{video['id']}/edit",
        json={
            "edit": {"op": "effect", "mode": "slow", "start": 1, "end": 2, "factor": 0.5},
            "output": {"mode": "replace"},
        },
    )
    job = wait_job(client, replaced.json()["id"])
    assert job["status"] == "succeeded", job
    assert job["result_video_id"] == video["id"]
    with client.app.state.sessionmaker() as db:
        output = db.get(Video, video["id"])
        assert output is not None and abs(output.duration - 5) < 0.1
        assert abs_path(settings, output.file_path).is_file()


@pytest.mark.parametrize("factor", [0.25, 0.1])
def test_very_slow_interval_uses_tempo_chain(tmp_path: Path, factor: float) -> None:
    src = ramp(tmp_path / "ramp.mkv")
    out, actual = render(
        src, tmp_path / "slow", EffectParams(mode="slow", start=1, end=2, factor=factor, crf=0)
    )
    repeats = round(1 / factor)
    assert actual["output_duration"] == 3 + repeats
    assert_frames(
        out,
        frame_values(src),
        list(range(10)) + [n for n in range(10, 20) for _ in range(repeats)] + list(range(20, 40)),
    )
    samples = audio_values(out)
    assert power(samples, 1.25, 440) > 0.14
    assert power(samples, 1 + repeats + 0.25, 660) > 0.14


@pytest.mark.parametrize("mode", ["reverse", "freeze", "slow"])
def test_audio_delay_is_preserved_in_effect_timeline(tmp_path: Path, mode: str) -> None:
    src = tmp_path / "delayed.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=64x48:r=10:d=2",
            "-itsoffset",
            "0.6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1.4",
            "-c:v",
            "ffv1",
            "-c:a",
            "pcm_s16le",
            str(src),
        ],
        check=True,
    )
    info = asyncio.run(probe("ffprobe", str(src)))
    assert abs(info.audio_delay - 0.6) < 0.01
    out, _ = render(
        src,
        tmp_path / mode,
        EffectParams(mode=mode, start=0, end=2, duration=0.5, factor=0.5, crf=0),
    )
    samples = audio_values(out)
    if mode == "reverse":
        silent, tone = 1.6, 0.3
    elif mode == "freeze":
        silent, tone = 0.8, 1.4
    else:
        silent, tone = 0.7, 2
    assert power(samples, silent, 440) < 0.001
    assert power(samples, tone, 440) > 0.04


def test_freeze_before_delayed_video_preserves_audio_and_leading_canvas(tmp_path: Path) -> None:
    src = tmp_path / "late-video.mkv"
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
            "testsrc2=s=64x48:r=10:d=1.4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
            "-c:v",
            "ffv1",
            "-c:a",
            "pcm_s16le",
            str(src),
        ],
        check=True,
    )
    info = asyncio.run(probe("ffprobe", str(src)))
    assert abs(info.video_delay - 0.6) < 0.01 and info.audio_delay == 0
    out, actual = render(
        src, tmp_path / "freeze", EffectParams(mode="freeze", start=0.3, duration=0.5, crf=0)
    )
    assert actual["output_duration"] == 2.5
    # Cloning the leading video canvas plus the inserted freeze gives the same first frame.
    raw = subprocess.check_output(
        ["ffmpeg", "-v", "error", "-i", str(out), "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    )
    area = 64 * 48
    assert all(raw[i * area : (i + 1) * area] == raw[:area] for i in range(11))
    samples = audio_values(out)
    assert power(samples, 0.1, 440) > 0.04
    assert power(samples, 0.4, 440) < 0.001
    assert power(samples, 1.4, 440) > 0.04
