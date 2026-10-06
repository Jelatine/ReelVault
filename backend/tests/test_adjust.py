from __future__ import annotations

import asyncio
import hashlib
import statistics
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from reelvault.config import Settings
from reelvault.library import abs_path
from reelvault.media.adjust import plan_adjust
from reelvault.media.ffmpeg import ffmpeg_args, run_command
from reelvault.media.luts import parse_cube
from reelvault.media.ops import AdjustParams
from reelvault.media.probe import probe
from reelvault.models import MediaAsset, Video

from .conftest import upload_ready, wait_job


def cube(dimension: int = 3) -> bytes:
    rows = [(1 - r, 1 - g, 1 - b) for b in (0, 1) for g in (0, 1) for r in (0, 1)]
    if dimension == 1:
        rows = [(1, 1, 1), (0, 0, 0)]
    return (
        f"LUT_{dimension}D_SIZE 2\n" + "\n".join(" ".join(map(str, row)) for row in rows)
    ).encode()


def make_source(path: Path, filt: str) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", filt, "-c:v", "ffv1", str(path)],
        check=True,
    )
    return path


def frames(path: Path, fmt: str = "gray") -> bytes:
    return subprocess.check_output(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", fmt, "-"]
    )


def process(
    src: Path, tmp: Path, params: AdjustParams, lut: Path | None = None, dimension: int = 3
) -> Path:
    tmp.mkdir()
    out = tmp / "out.mp4"

    async def run() -> None:
        info = await probe("ffprobe", str(src))
        plan = plan_adjust(params, (src, info), out, tmp, lut, dimension)
        assert len(plan.commands) == (2 if params.stabilize else 1)
        for command in plan.commands:
            await run_command(ffmpeg_args("ffmpeg", command), cwd=plan.cwd)

    asyncio.run(run())
    return out


def test_real_brightness_contrast_saturation(tmp_path: Path) -> None:
    src = make_source(tmp_path / "gray.mkv", "color=c=gray:s=64x48:r=10:d=1")
    baseline = statistics.mean(frames(src))
    bright = process(src, tmp_path / "bright", AdjustParams(brightness=0.2, crf=0))
    dark = process(src, tmp_path / "dark", AdjustParams(brightness=-0.2, crf=0))
    assert statistics.mean(frames(bright)) > baseline + 40
    assert statistics.mean(frames(dark)) < baseline - 40
    bars = make_source(tmp_path / "bars.mkv", "smptebars=s=64x48:r=10:d=1")
    high = process(bars, tmp_path / "contrast", AdjustParams(contrast=1.5, crf=0))
    assert statistics.pstdev(frames(high)) > statistics.pstdev(frames(bars)) * 1.2
    mono = process(bars, tmp_path / "mono", AdjustParams(saturation=0, crf=0))
    rgb = frames(mono, "rgb24")
    assert max(abs(rgb[i] - rgb[i + 1]) for i in range(0, len(rgb), 3)) <= 3


@pytest.mark.parametrize("dimension", [1, 3])
def test_lut_upload_render_hash_history_and_references(
    client: TestClient,
    samples: dict[str, Path],
    settings: Settings,
    tmp_path: Path,
    dimension: int,
) -> None:
    r = client.post("/api/lut-assets", files={"file": ("中文 ':[].cube", cube(dimension))})
    assert r.status_code == 200, r.text
    asset = r.json()
    assert asset["meta"]["dimension"] == dimension
    assert client.get("/api/lut-assets").json()[0]["id"] == asset["id"]
    with client.app.state.sessionmaker() as db:
        stored = db.get(MediaAsset, asset["id"])
        assert stored is not None
        lut = abs_path(settings, stored.file_path)
        digest = stored.sha256
        assert hashlib.sha256(lut.read_bytes()).hexdigest() == digest
    src = make_source(tmp_path / "red.mkv", "color=c=red:s=64x48:r=10:d=1")
    out = process(
        src, tmp_path / "inverted", AdjustParams(lut_asset_id=asset["id"], crf=0), lut, dimension
    )
    pixels = frames(out, "rgb24")
    means = [statistics.mean(pixels[c::3]) for c in range(3)]
    assert means[0] < 20 and means[1] > 230 and means[2] > 230, means
    video = upload_ready(client, samples["a"])
    edit = {"op": "adjust", "lut_asset_id": asset["id"], "denoise": 2, "stabilize": True}
    preset = client.post("/api/edit-presets", json={"name": "调色防抖", "edit": edit})
    assert preset.status_code == 200, preset.text
    r = client.post(f"/api/videos/{video['id']}/edit", json={"edit": edit})
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job
    assert job["params"]["lut_sha256"] == digest
    result = job["result_video_id"]
    history = client.get(f"/api/videos/{result}/history").json()
    assert history["nodes"][0]["can_recreate"]
    replay = client.post(f"/api/videos/{result}/recreate")
    assert replay.status_code == 200, replay.text
    assert wait_job(client, replay.json()["id"])["status"] == "succeeded"
    assert client.delete(f"/api/lut-assets/{asset['id']}").status_code == 409
    batch = client.post(
        "/api/jobs/batch", json={"video_ids": [video["id"]], "preset_id": preset.json()["id"]}
    )
    assert batch.status_code == 200, batch.text
    batch_job = wait_job(client, batch.json()[0]["id"])
    assert batch_job["status"] == "succeeded", batch_job
    assert batch_job["params"]["lut_sha256"] == digest
    lut.write_bytes(b"changed")
    assert client.post(f"/api/videos/{result}/recreate").status_code == 409
    assert client.post(f"/api/videos/{video['id']}/edit", json={"edit": edit}).status_code == 400
    with client.app.state.sessionmaker() as db:
        derived = db.get(Video, result)
        assert derived is not None and derived.edit_params["stabilize"]


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"LUT_3D_SIZE 1\n",
        b"LUT_3D_SIZE 65\n",
        b"LUT_3D_SIZE 2\n0 0 0",
        b"LUT_1D_SIZE 2\n0 0 0\nnan 0 0",
        b"LUT_1D_SIZE 2\nDOMAIN_MIN 1 0 0\nDOMAIN_MAX 0 1 1\n0 0 0\n1 1 1",
        b"LUT_1D_SIZE 2\nLUT_3D_SIZE 2\n",
        b"LUT_1D_SIZE 2\n0 0 0\n1 1 1\n2 2 2",
    ],
)
def test_invalid_lut(data: bytes) -> None:
    with pytest.raises(ValueError):
        parse_cube(data)


def test_lut_crud_auth_cleanup_and_validation(
    anon: TestClient, client: TestClient, settings: Settings
) -> None:
    client.post("/api/auth/logout")
    assert anon.get("/api/lut-assets").status_code == 401
    from .conftest import login

    login(client)
    for name, data in [("wrong.txt", cube()), ("bad.cube", b"LUT_3D_SIZE 2\n")]:
        assert client.post("/api/lut-assets", files={"file": (name, data)}).status_code == 400
    assert not list(settings.tmp_dir.glob("lut-*"))
    asset = client.post("/api/lut-assets", files={"file": ("invert.cube", cube())}).json()
    assert client.delete(f"/api/lut-assets/{asset['id']}").status_code == 200
    assert client.get("/api/lut-assets").json() == []
    for args in [
        {},
        {"brightness": float("nan")},
        {"denoise": 21},
        {"stabilize": True, "shakiness": 8, "accuracy": 4},
    ]:
        with pytest.raises(ValidationError):
            AdjustParams(**args)


def test_real_denoise_reduces_noise(tmp_path: Path) -> None:
    src = make_source(tmp_path / "noise.mkv", "color=c=gray:s=128x96:r=20:d=2,noise=alls=20:allf=t")
    out = process(src, tmp_path / "denoise", AdjustParams(denoise=12, crf=0))
    before, after = frames(src), frames(out)
    assert abs(statistics.mean(after) - statistics.mean(before)) < 3
    assert statistics.pstdev(after) < statistics.pstdev(before) * 0.7


def test_real_two_pass_stabilization_reduces_motion(tmp_path: Path) -> None:
    # Detailed background provides motion features; translating crop simulates hand jitter.
    src = make_source(
        tmp_path / "shake.mkv",
        "testsrc2=s=384x288:r=20:d=3,crop=320:240:x='32+10*sin(n*2)':y='24+8*cos(n*2)'",
    )
    out = process(src, tmp_path / "stable", AdjustParams(stabilize=True, smoothing=20, crf=0))
    assert (out.parent / "transforms.trf").stat().st_size > 100

    def motion(path: Path) -> float:
        # Estimate translation over a central region, ignoring moving testsrc details.
        raw = frames(path)
        area = 320 * 240
        errors = []
        for n in range(5, 50):
            a, b = raw[(n - 1) * area : n * area], raw[n * area : (n + 1) * area]
            best = (float("inf"), 0, 0)
            for dy in range(-20, 21, 2):
                for dx in range(-20, 21, 2):
                    error = sum(
                        abs(a[y * 320 + x] - b[(y + dy) * 320 + x + dx])
                        for y in range(40, 200, 16)
                        for x in range(40, 280, 16)
                    )
                    if error < best[0]:
                        best = (error, dx, dy)
            errors.append(abs(best[1]) + abs(best[2]))
        return statistics.mean(errors)

    before, after = motion(src), motion(out)
    assert after < before * 0.6, (before, after)


@pytest.mark.parametrize("dimension", [1, 3])
def test_lut_nondefault_domain_is_applied(tmp_path: Path, dimension: int) -> None:
    data = cube(dimension).decode().splitlines()
    data.insert(1, "DOMAIN_MAX 2 2 2")
    normalized, _ = parse_cube("\n".join(data).encode())
    lut = tmp_path / "domain.cube"
    lut.write_bytes(normalized)
    src = make_source(tmp_path / "red.mkv", "color=c=red:s=64x48:r=10:d=1")
    out = process(
        src, tmp_path / "domain", AdjustParams(lut_asset_id="a" * 32, crf=0), lut, dimension
    )
    red = statistics.mean(frames(out, "rgb24")[::3])
    assert 115 < red < 140, red


def test_asset_metadata_migration_preserves_old_assets(tmp_path: Path) -> None:
    import sqlite3

    from alembic import command

    from reelvault.db import make_engine
    from reelvault.migrate import alembic_config, upgrade

    database = tmp_path / "old.sqlite3"
    engine = make_engine(database)
    config = alembic_config(str(engine.url))
    command.upgrade(config, "0011")
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO media_assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "a" * 32,
                "image",
                "old.png",
                "assets/old.png",
                "b" * 64,
                23,
                0,
                0,
                "2026-10-05 00:00:00",
            ),
        )
    upgrade(engine)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT name, meta FROM media_assets").fetchone() == ("old.png", "{}")
    command.downgrade(config, "0011")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT name FROM media_assets").fetchone() == ("old.png",)
    engine.dispose()
