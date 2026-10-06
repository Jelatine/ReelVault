from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reelvault.config import Settings
from reelvault.media.ffmpeg import ffmpeg_args, run_command
from reelvault.media.ops import SubtitleParams
from reelvault.media.probe import probe
from reelvault.media.subtitles import plan_subtitle
from reelvault.models import Video

from .conftest import upload_ready, wait_job, wait_ready

SRT = "1\n00:00:00,500 --> 00:00:02,000\nHello subtitles\n"
VTT = "WEBVTT\n\n00:00:00.500 --> 00:00:02.000\nHello subtitles\n"
ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 240
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,24,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.50,0:00:02.00,Default,,0,0,0,,Hello subtitles
"""  # noqa: E501


@pytest.mark.parametrize("ext,content", [("srt", SRT), ("vtt", VTT), ("ass", ASS)])
def test_upload_convert_attach_remove_and_delete(
    client: TestClient, samples: dict[str, Path], ext: str, content: str
) -> None:
    video = upload_ready(client, samples["a"])
    r = client.post(
        "/api/subtitle-assets", files={"file": (f"sub.{ext}", content.encode(), "text/plain")}
    )
    assert r.status_code == 200, r.text
    asset_id = r.json()["id"]
    vtt = client.get(f"/api/subtitle-assets/{asset_id}/vtt")
    assert vtt.status_code == 200 and vtt.headers["content-type"].startswith("text/vtt")
    assert "00:00.500 --> 00:02.000" in vtt.text and "Hello subtitles" in vtt.text
    assert client.get("/api/subtitle-assets").json()[0]["id"] == asset_id
    r = client.post(
        f"/api/videos/{video['id']}/subtitles",
        json={"asset_id": asset_id, "label": "English", "language": "en"},
    )
    assert r.status_code == 200, r.text
    track_id = r.json()["id"]
    tracks = client.get(f"/api/videos/{video['id']}/subtitles").json()
    assert tracks[0]["label"] == "English" and tracks[0]["playable"]
    assert client.delete(f"/api/subtitle-assets/{asset_id}").status_code == 409
    assert client.delete(f"/api/videos/{video['id']}/subtitles/{track_id}").status_code == 200
    assert client.delete(f"/api/subtitle-assets/{asset_id}").status_code == 200
    assert client.get(f"/api/subtitle-assets/{asset_id}/vtt").status_code == 404


def test_invalid_subtitles_encoding_and_size(client: TestClient, settings: Settings) -> None:
    for name, content in (("sub.exe", b"bad"), ("sub.srt", b"bad"), ("sub.vtt", b"WEBVTT\n")):
        assert (
            client.post("/api/subtitle-assets", files={"file": (name, content)}).status_code == 400
        )
    assert (
        client.post(
            "/api/subtitle-assets", files={"file": ("big.srt", b"x" * (5 * 1024 * 1024 + 1))}
        ).status_code
        == 413
    )
    assert not list(settings.assets_dir.iterdir())
    chinese = SRT.replace("Hello subtitles", "中文字幕")
    assert (
        client.post(
            "/api/subtitle-assets", files={"file": ("sub.srt", chinese.encode("gb18030"))}
        ).status_code
        == 400
    )
    r = client.post(
        "/api/subtitle-assets",
        data={"encoding": "gb18030"},
        files={"file": ("sub.srt", chinese.encode("gb18030"))},
    )
    assert r.status_code == 200, r.text
    assert "中文字幕" in client.get(f"/api/subtitle-assets/{r.json()['id']}/vtt").text
    client.cookies.clear()
    assert client.get("/api/subtitle-assets").status_code == 401


def test_mkv_text_tracks_extract_and_burn_external_and_embedded(
    client: TestClient, samples: dict[str, Path], tmp_path: Path, settings: Settings
) -> None:
    sub = tmp_path / "english.srt"
    sub.write_text(SRT)
    mkv = tmp_path / "subs.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(samples["a"]),
            "-i",
            str(sub),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-map",
            "1:s:0",
            "-c",
            "copy",
            "-c:s",
            "srt",
            "-metadata:s:s:0",
            "language=eng",
            "-metadata:s:s:0",
            "title=English embedded",
            str(mkv),
        ],
        check=True,
    )
    video = upload_ready(client, mkv)
    tracks = client.get(f"/api/videos/{video['id']}/subtitles").json()
    assert len(tracks) == 1 and tracks[0]["codec"] == "subrip" and tracks[0]["language"] == "eng"
    assert "Hello subtitles" in client.get(tracks[0]["url"]).text
    r = client.post(
        f"/api/videos/{video['id']}/edit",
        json={"edit": {"op": "subtitle", "embedded_index": tracks[0]["embedded_index"]}},
    )
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job
    output = wait_ready(client, job["result_video_id"])
    assert output["edit_params"]["embedded_index"] == tracks[0]["embedded_index"]

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
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-vf",
                "crop=320:60:0:180",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "pipe:1",
            ]
        )

    original = pixels(mkv)
    with client.app.state.sessionmaker() as db:
        burned_path = settings.data_dir / db.get(Video, output["id"]).file_path
    burned = pixels(burned_path)
    assert sum(abs(a - b) for a, b in zip(original, burned, strict=True)) / len(original) > 2
    # File paths with quotes/colon/Unicode remain argv paths; filter uses a safe relative alias.
    folder = tmp_path / "path 'quoted': 中文"
    folder.mkdir()
    asset = folder / "external.ass"
    asset.write_text(ASS)

    async def run() -> None:
        info = await probe("ffprobe", str(samples["a"]))
        plan = plan_subtitle(
            SubtitleParams(subtitle_asset_id="a" * 32),
            (samples["a"], info),
            folder / "out.mp4",
            folder,
            asset,
        )
        for args in plan.commands:
            await run_command(ffmpeg_args("ffmpeg", args), cwd=plan.cwd)
        assert (folder / "out.mp4").is_file()

    asyncio.run(run())


def test_subtitle_asset_preset_history_and_integrity(
    client: TestClient, samples: dict[str, Path], settings: Settings
) -> None:
    video = upload_ready(client, samples["a"])
    asset = client.post("/api/subtitle-assets", files={"file": ("sub.srt", SRT.encode())}).json()
    edit = {"op": "subtitle", "subtitle_asset_id": asset["id"]}
    preset = client.post("/api/edit-presets", json={"name": "字幕", "edit": edit})
    assert preset.status_code == 200, preset.text
    jobs = client.post(
        "/api/jobs/batch", json={"video_ids": [video["id"]], "preset_id": preset.json()["id"]}
    )
    job = wait_job(client, jobs.json()[0]["id"])
    assert job["status"] == "succeeded", job
    output = wait_ready(client, job["result_video_id"])
    recreated = client.post(f"/api/videos/{output['id']}/recreate")
    assert recreated.status_code == 200
    assert wait_job(client, recreated.json()["id"])["status"] == "succeeded"
    assert client.delete(f"/api/subtitle-assets/{asset['id']}").status_code == 409
    (settings.assets_dir / f"{asset['id']}.srt").write_text("changed")
    history = client.get(f"/api/videos/{output['id']}/history").json()["nodes"][0]
    assert not history["can_recreate"] and "字幕素材" in history["asset_error"]
    assert client.post(f"/api/videos/{output['id']}/recreate").status_code == 409


def test_mkv_bitmap_subtitle_canvas_is_scaled_into_video(
    client: TestClient, settings: Settings
) -> None:
    sample = Path(__file__).parent / "fixtures" / "pgs-canvas.mkv"
    video = upload_ready(client, sample)
    tracks = client.get(f"/api/videos/{video['id']}/subtitles").json()
    assert len(tracks) == 1
    track = tracks[0]
    assert track["codec"] == "hdmv_pgs_subtitle" and not track["playable"]
    assert track["url"] is None
    assert client.get(f"/api/videos/{video['id']}/subtitles/embedded/1").status_code == 400
    r = client.post(
        f"/api/videos/{video['id']}/edit",
        json={
            "edit": {"op": "subtitle", "embedded_index": track["embedded_index"]},
            "output": {"mode": "new", "title": "PGS"},
        },
    )
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job
    wait_ready(client, job["result_video_id"])
    with client.app.state.sessionmaker() as db:
        output = db.get(Video, job["result_video_id"])
        assert output is not None
        path = settings.data_dir / output.file_path
        assert output.width == 720 and output.height == 480
        assert abs(output.duration - 10.01) < 0.1
    pixels = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            "2",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
    )
    # The source is solid white. Visible glyphs must lower the average luminance.
    assert sum(pixels) / len(pixels) < 253
    assert min(pixels) < 100
