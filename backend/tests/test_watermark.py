from __future__ import annotations

import asyncio
import os
import struct
import subprocess
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reelvault.config import Settings
from reelvault.media.ffmpeg import ffmpeg_args, run_command
from reelvault.media.ops import WatermarkParams
from reelvault.media.probe import probe
from reelvault.media.watermark import FONT, plan_watermark
from reelvault.models import Video

from .conftest import upload_ready, wait_job, wait_ready


def png(width: int = 16, height: int = 8, rgba: bytes = b"\xff\x00\x00\x80") -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + rgba * width) * height))
        + chunk(b"IEND", b"")
    )


def pixels(path: Path) -> bytes:
    return subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            "1",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
    )


@pytest.fixture
def black(tmp_path: Path) -> Path:
    path = tmp_path / "black.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=320x240:d=2:r=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
    )
    return path


def test_image_upload_preview_format_size_and_auth(client: TestClient, settings: Settings) -> None:
    for name, data in [
        ("bad.svg", b"<svg/>"),
        ("bad.jpg", b"invalid"),
        ("empty.png", b""),
        ("wide.png", png(4097, 1)),
    ]:
        assert client.post("/api/image-assets", files={"file": (name, data)}).status_code == 400
    assert (
        client.post(
            "/api/image-assets", files={"file": ("big.png", b"x" * (10 * 1024 * 1024 + 1))}
        ).status_code
        == 413
    )
    assert not list(settings.assets_dir.iterdir())
    assert not list(settings.tmp_dir.glob("image-*"))
    r = client.post("/api/image-assets", files={"file": ("logo.png", png())})
    assert r.status_code == 200, r.text
    asset = r.json()
    stream = client.get(asset["url"])
    assert stream.status_code == 200 and stream.headers["content-type"] == "image/png"
    assert stream.content.startswith(b"\x89PNG")
    assert client.get("/api/image-assets").json()[0]["id"] == asset["id"]
    assert client.get("/api/image-assets/font").content == FONT.read_bytes()
    assert client.delete(f"/api/image-assets/{asset['id']}").status_code == 200
    assert client.get(asset["url"]).status_code == 404
    client.post("/api/auth/logout")
    assert client.get("/api/image-assets").status_code == 401
    assert client.get("/api/image-assets/font").status_code == 401


@pytest.mark.parametrize("opacity,red", [(0, 0), (0.5, 64), (1, 128)])
@pytest.mark.parametrize("logo_size", [(16, 8), (8, 64)])
def test_image_scaling_position_alpha_and_duration(
    client: TestClient,
    settings: Settings,
    black: Path,
    opacity: float,
    red: int,
    logo_size: tuple[int, int],
) -> None:
    video = upload_ready(client, black)
    asset = client.post("/api/image-assets", files={"file": ("alpha.png", png(*logo_size))}).json()
    edit = {
        "op": "watermark",
        "mode": "image",
        "image_asset_id": asset["id"],
        "position": "custom",
        "x": 25,
        "y": 75,
        "width_percent": 20,
        "opacity": opacity,
    }
    r = client.post(f"/api/videos/{video['id']}/edit", json={"edit": edit})
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job
    result = wait_ready(client, job["result_video_id"])
    assert abs(result["duration"] - 2) < 0.1 and result["width"] == 320
    with client.app.state.sessionmaker() as db:
        output = db.get(Video, result["id"])
        assert output is not None
        frame = pixels(settings.data_dir / output.file_path)

    def pixel(x: int, y: int) -> tuple[int, ...]:
        i = (y * 320 + x) * 3
        return tuple(frame[i : i + 3])

    # Landscape is 64×32 at (64,156); portrait is height-limited to 30×240.
    # Opacity multiplies the original 50% alpha in both cases.
    assert abs(pixel(80, 170)[0] - red) < 8
    assert max(pixel(80, 170)[1:]) < 8
    assert max(pixel(40, 170)) < 8 and max(pixel(140, 170)) < 8
    assert client.delete(f"/api/image-assets/{asset['id']}").status_code == 409


def test_chinese_text_and_literal_punctuation_with_safe_paths(black: Path, tmp_path: Path) -> None:
    # Windows forbids ':' in names; its drive letter still puts one in the path.
    tmp = tmp_path / ("Chinese 'quotes' 中文" if os.name == "nt" else "Chinese 'quotes': 中文")
    tmp.mkdir()
    out = tmp / "text.mp4"
    info = asyncio.run(probe("ffprobe", str(black)))
    params = WatermarkParams(
        text="中文臺灣 : ' \\ %{eif:1}\nSecond line",
        position="top-left",
        opacity=1,
        font_size=24,
        box=True,
    )
    plan = plan_watermark(params, (black, info), out, tmp)
    for command in plan.commands:
        asyncio.run(run_command(ffmpeg_args("ffmpeg", command), cwd=plan.cwd))
    frame = pixels(out)
    assert sum(frame) / len(frame) > 5
    assert max(frame) > 240
    assert (tmp / "watermark.txt").read_text() == params.text
    # The explicit bundled font must work without relying on a system font name.
    assert (tmp / "font.otf").read_bytes() == FONT.read_bytes()
    generated = asyncio.run(probe("ffprobe", str(out)))
    assert abs(generated.duration - 2) < 0.1 and generated.audio_codec == "aac"


def test_preset_batch_replay_and_changed_image_rejected(
    client: TestClient, settings: Settings, black: Path
) -> None:
    video = upload_ready(client, black)
    asset = client.post("/api/image-assets", files={"file": ("logo.png", png())}).json()
    edit = {"op": "watermark", "mode": "image", "image_asset_id": asset["id"]}
    preset = client.post("/api/edit-presets", json={"name": "水印", "edit": edit})
    assert preset.status_code == 200, preset.text
    jobs = client.post(
        "/api/jobs/batch", json={"video_ids": [video["id"]], "preset_id": preset.json()["id"]}
    )
    assert jobs.status_code == 200, jobs.text
    job = wait_job(client, jobs.json()[0]["id"])
    assert job["status"] == "succeeded", job
    output = wait_ready(client, job["result_video_id"])
    history = client.get(f"/api/videos/{output['id']}/history").json()["nodes"][0]
    assert history["can_recreate"]
    replay = client.post(f"/api/videos/{output['id']}/recreate")
    assert replay.status_code == 200, replay.text
    assert wait_job(client, replay.json()["id"])["status"] == "succeeded"
    assert client.delete(f"/api/image-assets/{asset['id']}").status_code == 409
    (settings.assets_dir / f"{asset['id']}.png").write_bytes(b"changed")
    assert client.post(f"/api/videos/{video['id']}/edit", json={"edit": edit}).status_code == 400
    history = client.get(f"/api/videos/{output['id']}/history").json()["nodes"][0]
    assert not history["can_recreate"] and "变化" in history["asset_error"]
    assert client.post(f"/api/videos/{output['id']}/recreate").status_code == 409
    for bad in [dict(mode="text", text=""), dict(mode="image"), dict(text="null\0")]:
        assert (
            client.post(
                f"/api/videos/{video['id']}/edit", json={"edit": {"op": "watermark", **bad}}
            ).status_code
            == 422
        )


@pytest.mark.parametrize("ext,codec", [("jpg", "mjpeg"), ("webp", "libwebp")])
def test_jpeg_and_webp_are_normalized_to_png(client: TestClient, ext: str, codec: str) -> None:
    data = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "image2pipe",
            "-c:v",
            "png",
            "-i",
            "pipe:0",
            "-frames:v",
            "1",
            "-c:v",
            codec,
            "-f",
            "image2pipe",
            "pipe:1",
        ],
        input=png(),
        capture_output=True,
        check=True,
    ).stdout
    response = client.post("/api/image-assets", files={"file": (f"logo.{ext}", data)})
    assert response.status_code == 200, response.text
    asset = response.json()
    result = client.get(asset["url"])
    assert result.headers["content-type"] == "image/png"
    assert result.content.startswith(b"\x89PNG")
