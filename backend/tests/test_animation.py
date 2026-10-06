from __future__ import annotations

import asyncio
import json
import struct
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from reelvault.config import Settings
from reelvault.media.animation import plan_animation
from reelvault.media.ffmpeg import ffmpeg_args, run_command
from reelvault.media.ops import AnimationParams, OpError
from reelvault.media.probe import probe
from reelvault.models import Video

from .conftest import upload_ready, wait_job


def webp_chunks(data: bytes) -> dict[bytes, list[bytes]]:
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    result: dict[bytes, list[bytes]] = {}
    offset = 12
    while offset + 8 <= len(data):
        tag, size = struct.unpack_from("<4sI", data, offset)
        result.setdefault(tag, []).append(data[offset + 8 : offset + 8 + size])
        offset += 8 + size + size % 2
    return result


@pytest.mark.parametrize(
    "fmt,loop", [("gif", True), ("gif", False), ("webp", True), ("webp", False)]
)
def test_real_export_clip_dimensions_timing_loop_and_download(
    client: TestClient, samples: dict[str, Path], settings: Settings, fmt: str, loop: bool
) -> None:
    video = upload_ready(client, samples["a"])
    with client.app.state.sessionmaker() as db:
        original = db.get(Video, video["id"])
        assert original is not None
        version = original.asset_version
    edit = {
        "op": "animation",
        "format": fmt,
        "start": 0.5,
        "end": 2,
        "fps": 10,
        "width": 160,
        "loop": loop,
        "lossless": fmt == "webp",
    }
    r = client.post(
        f"/api/videos/{video['id']}/edit",
        json={"edit": edit, "output": {"mode": "new", "title": "Clip export"}},
    )
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job
    assert job["has_result_file"] and job["result_video_id"] is None
    assert job["params"]["actual_range"] == {"start": 0.5, "end": 2}
    download = client.get(f"/api/jobs/{job['id']}/download")
    assert download.status_code == 200
    assert f"Clip export.{fmt}" in unquote(download.headers["content-disposition"])
    data = download.content
    path = settings.exports_dir / f"{job['id']}.{fmt}"
    assert path.read_bytes() == data
    with client.app.state.sessionmaker() as db:
        source = db.get(Video, video["id"])
        assert source is not None and source.asset_version == version
    if fmt == "gif":
        assert data[:6] in (b"GIF87a", b"GIF89a")
        assert struct.unpack_from("<HH", data, 6) == (160, 120)
        assert (b"NETSCAPE2.0" in data) == loop
        result = json.loads(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_format",
                    "-show_streams",
                    "-show_frames",
                    "-of",
                    "json",
                    str(path),
                ]
            )
        )
        assert len(result["frames"]) == 15
        assert abs(float(result["format"]["duration"]) - 1.5) < 0.02
        assert all(s["codec_type"] != "audio" for s in result["streams"])

        # First frame must come from the chosen start, not the beginning of the source.
        def rgb(file: Path, seek: str) -> bytes:
            return subprocess.check_output(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    seek,
                    "-i",
                    str(file),
                    "-vf",
                    "scale=160:120:flags=lanczos",
                    "-frames:v",
                    "1",
                    "-pix_fmt",
                    "rgb24",
                    "-f",
                    "rawvideo",
                    "pipe:1",
                ]
            )

        actual, expected = rgb(path, "0"), rgb(samples["a"], "0.52")
        assert sum(abs(a - b) for a, b in zip(actual, expected, strict=True)) / len(actual) < 8
    else:
        chunks = webp_chunks(data)
        header = chunks[b"VP8X"][0]
        assert int.from_bytes(header[4:7], "little") + 1 == 160
        assert int.from_bytes(header[7:10], "little") + 1 == 120
        assert struct.unpack_from("<H", chunks[b"ANIM"][0], 4)[0] == (0 if loop else 1)
        frames = chunks[b"ANMF"]
        assert len(frames) == 15
        assert abs(sum(int.from_bytes(f[12:15], "little") for f in frames) - 1500) <= 2
        assert all(b"VP8L" in f for f in frames)  # Lossless frames, not lossy VP8.
    client.post("/api/auth/logout")
    assert client.get(f"/api/jobs/{job['id']}/download").status_code == 401


def test_range_validation_clamp_preset_batch_and_replace_guard(
    client: TestClient, samples: dict[str, Path], settings: Settings
) -> None:
    video = upload_ready(client, samples["a"])
    for invalid in [
        {"start": 2, "end": 1},
        {"end": 1, "fps": 0},
        {"end": 1, "width": 4},
        {"end": 1, "format": "png"},
    ]:
        assert (
            client.post(
                f"/api/videos/{video['id']}/edit", json={"edit": {"op": "animation", **invalid}}
            ).status_code
            == 422
        )
    edit = {"op": "animation", "format": "gif", "start": 3, "end": 10, "fps": 5, "width": 128}
    assert (
        client.post(
            f"/api/videos/{video['id']}/edit", json={"edit": edit, "output": {"mode": "replace"}}
        ).status_code
        == 400
    )
    preset = client.post("/api/edit-presets", json={"name": "GIF", "edit": edit}).json()
    assert (
        client.post(
            "/api/jobs/batch",
            json={
                "video_ids": [video["id"]],
                "preset_id": preset["id"],
                "output": {"mode": "replace"},
            },
        ).status_code
        == 400
    )
    jobs = client.post(
        "/api/jobs/batch", json={"video_ids": [video["id"]], "preset_id": preset["id"]}
    )
    assert jobs.status_code == 200, jobs.text
    job = wait_job(client, jobs.json()[0]["id"])
    assert job["status"] == "succeeded", job
    assert len(list(settings.exports_dir.glob("*.gif"))) == 1
    assert job["params"]["actual_range"] == {"start": 3, "end": 4}
    # Failed ranges can be retried as independent export tasks with the same settings.
    failed = client.post(
        f"/api/videos/{video['id']}/edit", json={"edit": {**edit, "start": 5}}
    ).json()
    assert wait_job(client, failed["id"])["status"] == "failed"
    retried = client.post(f"/api/jobs/{failed['id']}/retry")
    assert retried.status_code == 200
    assert wait_job(client, retried.json()["id"])["status"] == "failed"


@pytest.mark.parametrize("sar", [1, 2])
def test_palette_plan_on_odd_width_and_non_square_pixels(
    samples: dict[str, Path], tmp_path: Path, sar: int
) -> None:
    source = tmp_path / "anamorphic.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(samples["a"]),
            "-vf",
            f"setsar={sar}",
            "-c:v",
            "libx264",
            "-c:a",
            "copy",
            str(source),
        ],
        check=True,
    )
    info = asyncio.run(probe("ffprobe", str(source)))
    p = AnimationParams(end=1, width=161, fps=7)
    plan = plan_animation(p, (source, info), tmp_path / "output.mp4", tmp_path)
    for command in plan.commands:
        asyncio.run(run_command(ffmpeg_args("ffmpeg", command)))
    assert (tmp_path / "palette.png").is_file()
    assert struct.unpack_from("<HH", (tmp_path / "output.gif").read_bytes(), 6) == (
        161,
        round(161 * 240 / (320 * sar)),
    )
    with pytest.raises(OpError):
        plan_animation(
            AnimationParams(start=5, end=6), (samples["a"], info), tmp_path / "bad", tmp_path
        )
