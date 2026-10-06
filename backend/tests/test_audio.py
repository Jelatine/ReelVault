from __future__ import annotations

import array
import asyncio
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reelvault.backup import BackupError, create_backup, restore_backup
from reelvault.config import Settings
from reelvault.main import create_app
from reelvault.media.ffmpeg import ffmpeg_args, run_command
from reelvault.media.ops import AudioParams, OpError, plan_audio
from reelvault.media.probe import probe

from .conftest import HEADERS, login, upload_ready, wait_job, wait_ready


@pytest.fixture
def music(tmp_path: Path) -> Path:
    path = tmp_path / "music.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=1",
            "-ar",
            "48000",
            str(path),
        ],
        check=True,
    )
    return path


def pcm(path: Path, start=0.0, duration=None) -> list[float]:
    args = ["ffmpeg", "-v", "error", "-ss", str(start), "-i", str(path)]
    if duration is not None:
        args += ["-t", str(duration)]
    data = subprocess.check_output(
        args + ["-vn", "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1"]
    )
    values = array.array("f")
    values.frombytes(data)
    return list(values)


def rms(values: list[float]) -> float:
    return math.sqrt(sum(v * v for v in values) / len(values))


def tone(values: list[float], frequency: int) -> float:
    return abs(
        sum(
            v
            * complex(
                math.cos(2 * math.pi * frequency * i / 48000),
                math.sin(2 * math.pi * frequency * i / 48000),
            )
            for i, v in enumerate(values)
        )
    ) / len(values)


def test_real_gain_fades_loudness_and_video_copy(samples: dict[str, Path], tmp_path: Path) -> None:
    async def render(params: AudioParams, name: str) -> Path:
        source = samples["a"]
        plan = plan_audio(params, (source, await probe("ffprobe", str(source))), tmp_path / name)
        await run_command(ffmpeg_args("ffmpeg", plan.commands[0]))
        return tmp_path / name

    quiet = asyncio.run(render(AudioParams(gain_db=-6), "quiet.mp4"))
    assert rms(pcm(quiet, 1, 1)) / rms(pcm(samples["a"], 1, 1)) == pytest.approx(0.501, abs=0.015)
    for path in (samples["a"], quiet):
        encoded = subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-c:v",
                "copy",
                "-f",
                "h264",
                "pipe:1",
            ]
        )
        if path == samples["a"]:
            original = encoded
        else:
            assert encoded == original
    faded = asyncio.run(render(AudioParams(fade_in=1, fade_out=1), "fade.mp4"))
    middle = rms(pcm(faded, 1.5, 0.5))
    assert rms(pcm(faded, 0.05, 0.1)) < middle * 0.2
    assert rms(pcm(faded, 3.85, 0.1)) < middle * 0.2
    normalized = asyncio.run(render(AudioParams(normalize=True, target_lufs=-16), "normal.mp4"))
    stats = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(normalized),
            "-vn",
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    measured = json.loads(re.findall(r"\{[^{}]+\}", stats.stderr)[-1])
    assert float(measured["input_i"]) == pytest.approx(-16, abs=0.8)


def test_real_replace_mix_loop_offset_and_silent_video(
    samples: dict[str, Path], music: Path, tmp_path: Path
) -> None:
    async def run() -> None:
        for mode, silent, loop, offset, name in [
            ("replace", False, False, 0, "replace"),
            ("mix", False, True, 0.5, "mix"),
            ("mix", True, True, 0, "silent"),
        ]:
            src = samples["portrait" if silent else "a"]
            info = await probe("ffprobe", str(src))
            output = tmp_path / f"{name}.mp4"
            params = AudioParams(
                mode=mode, audio_asset_id="a" * 32, music_gain_db=0, loop=loop, offset=offset
            )
            plan = plan_audio(params, (src, info), output, (music, 0))
            await run_command(ffmpeg_args("ffmpeg", plan.commands[0]))
            result = await probe("ffprobe", str(output))
            assert result.duration == pytest.approx(info.duration, abs=0.1)
            assert (
                result.audio_codec == "aac" and result.sample_rate == 48000 and result.channels == 2
            )
            if name == "replace":
                values = pcm(output, 0.2, 0.5)
                assert tone(values, 880) > tone(values, 440) * 50
                assert rms(pcm(output, 2, 0.5)) < 0.00001
            if name == "mix":
                early, late = pcm(output, 0.05, 0.2), pcm(output, 2, 0.5)
                assert tone(early, 440) > tone(early, 880) * 20
                assert tone(late, 440) > 0.02 and tone(late, 880) > 0.02
            if name == "silent":
                assert tone(pcm(output, 2, 0.5), 880) > 0.02
        with pytest.raises(OpError, match="没有音轨"):
            info = await probe("ffprobe", str(samples["portrait"]))
            plan_audio(AudioParams(), (samples["portrait"], info), tmp_path / "invalid.mp4")
        with pytest.raises(OpError, match="淡入"):
            plan_audio(
                AudioParams(fade_in=2, fade_out=2),
                (samples["portrait"], info),
                tmp_path / "invalid.mp4",
            )

    asyncio.run(run())


def upload_music(client: TestClient, path: Path) -> dict:
    r = client.post(
        "/api/audio-assets", files={"file": (path.name, path.read_bytes(), "audio/wav")}
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_asset_upload_validation_stream_delete_and_auth(
    client: TestClient, music: Path, samples: dict[str, Path], settings: Settings
) -> None:
    for name, data in (("music.txt", b"x"), ("music.wav", b""), ("broken.mp3", b"bad")):
        assert client.post("/api/audio-assets", files={"file": (name, data)}).status_code == 400
    settings.audio_upload_max_mb = 1
    assert (
        client.post(
            "/api/audio-assets", files={"file": ("large.wav", b"x" * (1024 * 1024 + 1))}
        ).status_code
        == 413
    )
    assert not list(settings.assets_dir.iterdir()) and not list(settings.tmp_dir.glob("audio-*"))
    asset = upload_music(client, music)
    assert asset["duration"] == pytest.approx(1, abs=0.01)
    assert client.get("/api/audio-assets").json()[0]["id"] == asset["id"]
    r = client.get(f"/api/audio-assets/{asset['id']}/stream", headers={"Range": "bytes=0-15"})
    assert r.status_code == 206 and r.content == music.read_bytes()[:16]
    assert client.delete(f"/api/audio-assets/{asset['id']}").status_code == 200
    assert client.get(f"/api/audio-assets/{asset['id']}/stream").status_code == 404
    assert not list(settings.assets_dir.iterdir())
    client.cookies.clear()
    assert client.get("/api/audio-assets").status_code == 401
    assert (
        client.post(
            "/api/audio-assets", files={"file": (music.name, music.read_bytes())}
        ).status_code
        == 401
    )


def test_audio_job_preset_history_and_asset_integrity(
    client: TestClient, samples: dict[str, Path], music: Path, settings: Settings
) -> None:
    video = upload_ready(client, samples["a"])
    asset = upload_music(client, music)
    edit = {
        "op": "audio",
        "mode": "mix",
        "audio_asset_id": asset["id"],
        "loop": True,
        "fade_out": 0.5,
    }
    preset = client.post("/api/edit-presets", json={"name": "背景音乐", "edit": edit})
    assert preset.status_code == 200, preset.text
    assert client.delete(f"/api/audio-assets/{asset['id']}").status_code == 409
    r = client.post(
        "/api/jobs/batch", json={"video_ids": [video["id"]], "preset_id": preset.json()["id"]}
    )
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()[0]["id"])
    assert job["status"] == "succeeded", job
    output = wait_ready(client, job["result_video_id"])
    history = client.get(f"/api/videos/{output['id']}/history").json()["nodes"][0]
    assert history["can_recreate"] and history["edit"]["audio_asset_id"] == asset["id"]
    recreated = client.post(f"/api/videos/{output['id']}/recreate")
    assert recreated.status_code == 200, recreated.text
    assert wait_job(client, recreated.json()["id"])["status"] == "succeeded"
    path = next(settings.assets_dir.iterdir())
    path.write_bytes(b"changed")
    assert not client.get(f"/api/videos/{output['id']}/history").json()["nodes"][0]["can_recreate"]
    assert client.post(f"/api/videos/{output['id']}/recreate").status_code == 409
    assert client.post(f"/api/videos/{video['id']}/edit", json={"edit": edit}).status_code == 400
    assert (
        client.post(
            "/api/edit-presets",
            json={"name": "坏素材", "edit": {**edit, "audio_asset_id": "0" * 32}},
        ).status_code
        == 400
    )


def test_backup_requires_and_restores_audio_assets(
    settings: Settings, tmp_path: Path, music: Path
) -> None:
    with TestClient(create_app(settings), headers=HEADERS) as client:
        login(client)
        asset = upload_music(client, music)
        archive = create_backup(settings, tmp_path / "audio.zip")
    target = Settings(data_dir=tmp_path / "restored", update_check=False)
    target.ensure_dirs()
    with pytest.raises(BackupError, match="缺少"):
        restore_backup(archive, target)
    shutil.copytree(settings.assets_dir, target.assets_dir, dirs_exist_ok=True)
    copied = next(target.assets_dir.iterdir())
    copied.write_bytes(b"changed")
    with pytest.raises(BackupError, match="校验"):
        restore_backup(archive, target)
    shutil.copytree(settings.assets_dir, target.assets_dir, dirs_exist_ok=True)
    restore_backup(archive, target)
    with TestClient(create_app(target), headers=HEADERS) as client:
        login(client)
        assert client.get(f"/api/audio-assets/{asset['id']}/stream").content == music.read_bytes()


def test_original_audio_start_delay_is_preserved(
    samples: dict[str, Path], music: Path, tmp_path: Path
) -> None:
    source = tmp_path / "delayed.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(samples["a"]),
            "-itsoffset",
            "1",
            "-i",
            str(music),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-t",
            "4",
            str(source),
        ],
        check=True,
    )

    async def run() -> None:
        info = await probe("ffprobe", str(source))
        assert info.audio_delay > 0.9
        target = tmp_path / "adjusted.mp4"
        plan = plan_audio(AudioParams(gain_db=-3), (source, info), target)
        await run_command(ffmpeg_args("ffmpeg", plan.commands[0]))
        assert rms(pcm(target, 0.1, 0.5)) < 0.00001
        assert rms(pcm(target, 1.2, 0.5)) > 0.02
        assert (await probe("ffprobe", str(target))).duration == pytest.approx(4, abs=0.1)

    asyncio.run(run())
