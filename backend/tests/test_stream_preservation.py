from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from reelvault.config import Settings
from reelvault.media import ops
from reelvault.media.encoding import hardware_command
from reelvault.media.probe import MediaInfo
from reelvault.models import Video

from .conftest import upload_ready, wait_job, wait_ready

SRT = "1\n00:00:00,500 --> 00:00:03,000\nHello subtitles\n"


def streams(path: Path) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)],
        check=True,
        capture_output=True,
    )
    return list(json.loads(result.stdout)["streams"])


def kinds(path: Path) -> list[str]:
    return [s["codec_type"] for s in streams(path)]


@pytest.fixture(scope="module")
def multitrack(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """MKV with two audio tracks and a text subtitle, as from a disc rip or recorder."""
    folder = tmp_path_factory.mktemp("multitrack")
    srt = folder / "sub.srt"
    srt.write_text(SRT)
    out = folder / "multitrack.mkv"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=4",
            "-i", str(srt),
            "-map", "0", "-map", "1", "-map", "2", "-map", "3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "25",
            "-c:a:0", "aac", "-c:a:1", "ac3", "-c:s", "srt",
            "-metadata:s:a:1", "language=eng", str(out),
        ],
        check=True,
    )  # fmt: skip
    return out


@pytest.fixture(scope="module")
def hevc10(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("hevc10") / "hevc10.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le",
            "-tag:v", "hvc1", "-x265-params", "log-level=error",
            "-c:a", "aac", "-shortest", str(out),
        ],
        check=True,
    )  # fmt: skip
    return out


def edit_result(
    client: TestClient, settings: Settings, video_id: str, edit: dict[str, Any]
) -> Path:
    r = client.post(f"/api/videos/{video_id}/edit", json={"edit": edit, "output": {}})
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job["error"]
    wait_ready(client, job["result_video_id"])
    with client.app.state.sessionmaker() as db:
        return settings.data_dir / db.get(Video, job["result_video_id"]).file_path


@pytest.mark.parametrize(
    "edit",
    [
        {"op": "rotate", "angle": 90},
        {"op": "crop", "x": 0, "y": 0, "width": 160, "height": 120},
        {"op": "trim", "mode": "precise", "segments": [{"start": 0.2, "end": 3}]},
        {"op": "compress", "preset": "ultrafast"},
        {"op": "convert", "format": "mp4"},
    ],
)
def test_reencode_keeps_audio_tracks_and_text_subtitles(
    client: TestClient, settings: Settings, multitrack: Path, edit: dict[str, Any]
) -> None:
    source = upload_ready(client, multitrack)
    out = edit_result(client, settings, source["id"], edit)
    found = streams(out)
    assert [s["codec_type"] for s in found] == ["video", "audio", "audio", "subtitle"]
    assert found[3]["codec_name"] == "mov_text"
    # Track order and language survive; the primary track stays first.
    assert found[2].get("tags", {}).get("language") == "eng"


def test_stream_copy_keeps_every_track(
    client: TestClient, settings: Settings, multitrack: Path
) -> None:
    source = upload_ready(client, multitrack)
    out = edit_result(client, settings, source["id"], {"op": "convert", "format": "mkv"})
    found = streams(out)
    assert [s["codec_type"] for s in found] == ["video", "audio", "audio", "subtitle"]
    assert [s["codec_name"] for s in found[1:]] == ["aac", "ac3", "subrip"]


def test_fast_trim_keeps_audio_tracks_without_subtitles(
    client: TestClient, settings: Settings, multitrack: Path, tmp_path: Path
) -> None:
    # Copied subtitle packets would keep cues from before the cut and overrun the end.
    mp4 = tmp_path / "multitrack.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(multitrack), "-map", "0", "-c", "copy"]
        + ["-c:s", "mov_text", str(mp4)],
        check=True,
    )
    assert kinds(mp4) == ["video", "audio", "audio", "subtitle"]
    source = upload_ready(client, mp4)
    edit = {"op": "trim", "mode": "fast", "segments": [{"start": 1, "end": 3}]}
    out = edit_result(client, settings, source["id"], edit)
    assert [s["codec_name"] for s in streams(out)][1:] == ["aac", "ac3"]


def test_mute_keeps_subtitles(client: TestClient, settings: Settings, multitrack: Path) -> None:
    source = upload_ready(client, multitrack)
    out = edit_result(client, settings, source["id"], {"op": "mute"})
    assert kinds(out) == ["video", "subtitle"]


def test_speed_drops_subtitles_but_keeps_audio_tracks(
    client: TestClient, settings: Settings, multitrack: Path
) -> None:
    source = upload_ready(client, multitrack)
    out = edit_result(client, settings, source["id"], {"op": "speed", "factor": 2})
    assert kinds(out) == ["video", "audio", "audio"]


def test_hevc_10bit_source_stays_hevc_10bit(
    client: TestClient, settings: Settings, hevc10: Path
) -> None:
    source = upload_ready(client, hevc10)
    for edit in (
        {"op": "rotate", "angle": 90},
        {"op": "trim", "segments": [{"start": 0, "end": 1}, {"start": 2, "end": 3}]},
        {"op": "compress", "codec": "h265", "preset": "ultrafast"},
    ):
        video = streams(edit_result(client, settings, source["id"], edit))[0]
        assert (video["codec_name"], video["pix_fmt"]) == ("hevc", "yuv420p10le"), edit
        assert video["codec_tag_string"] == "hvc1"
    # H.264 compression remains 8-bit for browser playback.
    edit = {"op": "compress", "codec": "h264", "preset": "ultrafast"}
    video = streams(edit_result(client, settings, source["id"], edit))[0]
    assert (video["codec_name"], video["pix_fmt"]) == ("h264", "yuv420p")


def test_video_target() -> None:
    h264 = MediaInfo(video_codec="h264", pix_fmt="yuv420p")
    hevc8 = MediaInfo(video_codec="hevc", pix_fmt="yuv420p")
    hevc10 = MediaInfo(video_codec="hevc", pix_fmt="yuv420p10le")
    hdr = MediaInfo(video_codec="hevc", pix_fmt="yuv420p10le", color_transfer="smpte2084")
    assert ops.video_target(h264) == ops.VideoTarget("h264", False)
    assert ops.video_target(hevc8) == ops.VideoTarget("h265", False)
    assert ops.video_target(hevc10) == ops.VideoTarget("h265", True)
    # HDR is tone mapped to 8-bit SDR; mixed sources fall back to H.264.
    assert ops.video_target(hdr) == ops.VideoTarget("h265", False)
    assert ops.video_target(hevc10, h264) == ops.VideoTarget("h264", False)
    args = ops.video_target(hevc10).args(20)
    assert args[args.index("-crf") + 1] == str(20 + ops.X265_CRF_OFFSET)
    assert args[args.index("-pix_fmt") + 1] == "yuv420p10le"


def test_lossless_merge_maps_primary_tracks_only(tmp_path: Path) -> None:
    info = MediaInfo(
        video_codec="h264",
        audio_codec="aac",
        video_index=0,
        audio_index=1,
        duration=2,
        extra={"audio_streams": [{"index": 1, "codec": "aac"}, {"index": 2, "codec": "aac"}]},
    )
    params = ops.MergeParams(video_ids=["a", "b"], mode="lossless")
    sources = [(tmp_path / "a.mp4", info), (tmp_path / "b.mp4", info)]
    plan = ops.plan_merge(params, sources, tmp_path / "out.mp4", tmp_path)
    command = plan.commands[0]
    assert [command[i + 1] for i, a in enumerate(command) if a == "-map"] == ["0:0", "0:1"]


@pytest.mark.parametrize(
    ("family", "pix_fmt"),
    [("videotoolbox", "p010le"), ("qsv", "p010le"), ("nvenc", "p010le")],
)
def test_hardware_hevc_10bit_uses_main10(family: str, pix_fmt: str) -> None:
    args = ["-i", "in.mp4", *ops.VideoTarget("h265", True).args(20), "out.mp4"]
    changed = hardware_command(args, family, "")
    assert changed[changed.index("-pix_fmt") + 1] == pix_fmt
    assert changed[changed.index("-profile:v") + 1] == "main10"
    eight_bit = hardware_command(
        ["-i", "in.mp4", *ops.VideoTarget("h265").args(20), "out.mp4"], family, ""
    )
    assert "-profile:v" not in eight_bit and "p010le" not in eight_bit


def test_vaapi_hevc_10bit_uploads_p010() -> None:
    args = ["-i", "in.mp4", "-vf", "hflip", *ops.VideoTarget("h265", True).args(20), "out.mp4"]
    changed = hardware_command(args, "vaapi", "/dev/dri/renderD128")
    assert changed[changed.index("-vf") + 1] == "hflip,format=p010,hwupload"
    assert changed[changed.index("-profile:v") + 1] == "main10"
