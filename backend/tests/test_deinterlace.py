from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reelvault.config import Settings
from reelvault.media.color import SDR_TAGS, sdr_commands, source_filter
from reelvault.media.probe import MediaInfo
from reelvault.models import Video

from .conftest import upload_ready, wait_job, wait_ready

BWDIF = "bwdif=mode=send_frame:parity=auto:deint=interlaced"


def field_order(path: Path) -> str:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0"]
        + ["-show_entries", "stream=field_order", "-of", "csv=p=0", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture(scope="module")
def interlaced(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("interlaced") / "interlaced.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-vf", "setfield=tff", "-c:v", "libx264", "-flags", "+ildct+ilme",
            "-x264-params", "tff=1", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out),
        ],
        check=True,
    )  # fmt: skip
    assert field_order(out) == "tt"
    return out


def test_source_filter_combines_deinterlace_and_tone_mapping() -> None:
    assert source_filter(MediaInfo(field_order="progressive")) == ""
    assert source_filter(MediaInfo(field_order="bb")) == BWDIF
    hdr = MediaInfo(field_order="tt", color_transfer="arib-std-b67")
    assert source_filter(hdr).startswith(BWDIF + ",zscale=tin=arib-std-b67")


def test_interlaced_commands_are_deinterlaced_without_sdr_tags(tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    info = MediaInfo(field_order="tt")
    vf, speed, graph, copy = sdr_commands(
        [
            ["-i", str(src), "-vf", "transpose=clock", "out.mp4"],
            ["-i", str(src), "-filter:v", "setpts=PTS/2", "out.mp4"],
            ["-i", str(src), "-filter_complex", "[0:0]scale=2:2[v]", "-map", "[v]", "out.mp4"],
            ["-i", str(src), "-c", "copy", "out.mkv"],
        ],
        [(src, info)],
    )
    assert vf[vf.index("-vf") + 1] == f"{BWDIF},transpose=clock"
    # Only one video filter option is honoured, so it must be extended in place.
    assert speed.count("-vf") == 0 and speed[speed.index("-filter:v") + 1].startswith(BWDIF)
    assert graph[graph.index("-filter_complex") + 1].startswith(f"[0:0]{BWDIF}[rv_sdr_0_0]")
    assert copy == ["-i", str(src), "-c", "copy", "out.mkv"]
    assert not any(tag in args for args in (vf, speed, graph) for tag in SDR_TAGS[::2])


def test_reencoded_edits_are_progressive_and_copies_keep_fields(
    client: TestClient, settings: Settings, interlaced: Path
) -> None:
    source = upload_ready(client, interlaced)
    with client.app.state.sessionmaker() as db:
        assert db.get(Video, source["id"]).meta["field_order"] == "tt"
    r = client.post(f"/api/videos/{source['id']}/edit", json={"edit": {"op": "rotate"}})
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job["error"]
    wait_ready(client, job["result_video_id"])
    with client.app.state.sessionmaker() as db:
        out = settings.data_dir / db.get(Video, job["result_video_id"]).file_path
    assert field_order(out) == "progressive"
    logs = client.get(f"/api/jobs/{job['id']}/logs").json()
    assert any("去隔行" in entry["message"] for entry in logs)

    # Lossless operations keep the original fields.
    r = client.post(f"/api/videos/{source['id']}/edit", json={"edit": {"op": "mute"}})
    job = wait_job(client, r.json()["id"])
    with client.app.state.sessionmaker() as db:
        out = settings.data_dir / db.get(Video, job["result_video_id"]).file_path
    assert field_order(out) == "tt"
