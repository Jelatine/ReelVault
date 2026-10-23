from __future__ import annotations

import asyncio
import os
import subprocess

import pytest

from reelvault.media import derive, hls, ops
from reelvault.media.color import sdr_commands
from reelvault.media.encoding import EncoderRuntime, detect_encoders
from reelvault.media.ffmpeg import ProcessHandle, ffmpeg_args, run_command
from reelvault.media.probe import MediaInfo, probe
from reelvault.media.validation import validate_output

from .conftest import upload_ready, wait_job, wait_ready


@pytest.fixture(params=["smpte2084", "arib-std-b67"])
def hdr(request, tmp_path):
    path = tmp_path / "hdr.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=128x72:rate=12:duration=2",
            "-vf",
            "zscale=pin=bt709:tin=bt709:min=bt709:rin=limited:"
            f"p=bt2020:t={request.param}:m=bt2020nc,format=yuv420p10le",
            "-c:v",
            "libx265",
            "-preset",
            "ultrafast",
            "-x265-params",
            "pools=1:frame-threads=1:log-level=error",
            "-color_trc",
            request.param,
            "-color_primaries",
            "bt2020",
            "-colorspace",
            "bt2020nc",
            str(path),
        ],
        check=True,
    )
    return path


def test_hdr_edit_and_copy_preserve_original(client, hdr):
    original = upload_ready(client, hdr)
    assert original["media"]["color_transfer"] in {"smpte2084", "arib-std-b67"}
    result = client.post(
        f"/api/videos/{original['id']}/edit",
        json={"edit": {"op": "rotate", "angle": 90, "mode": "reencode"}},
    )
    job = wait_job(client, result.json()["id"])
    assert job["status"] == "succeeded", job["error"]
    edited = wait_ready(client, job["result_video_id"])
    assert edited["media"]["color_transfer"] == "bt709"
    assert edited["media"]["pix_fmt"] == "yuv420p"
    assert (edited["width"], edited["height"]) == (72, 128)
    assert client.get(f"/api/videos/{original['id']}/download").content == hdr.read_bytes()
    copy = ops.plan_mute((hdr, asyncio.run(probe("ffprobe", str(hdr)))), hdr.with_name("muted.mp4"))
    assert (
        sdr_commands(copy.commands, [(hdr, asyncio.run(probe("ffprobe", str(hdr))))])
        == copy.commands
    )


def test_hdr_derivatives_and_hls(hdr, tmp_path):
    async def scenario():
        info = await probe("ffprobe", str(hdr))
        handle = ProcessHandle(threads=1)
        assert info.is_hdr and not info.browser_playable
        playable = tmp_path / "playable.mp4"
        await derive.make_playable("ffmpeg", hdr, info, playable, handle)
        preview = tmp_path / "preview.mp4"
        await derive.make_preview("ffmpeg", hdr, info, preview, handle)
        for path in (playable, preview):
            produced = await probe("ffprobe", str(path))
            assert produced.color_transfer == "bt709"
            assert produced.color_primaries == "bt709"
            assert produced.color_space == "bt709"
            await validate_output("ffmpeg", "ffprobe", path, expected_duration=2)
        await derive.extract_frame("ffmpeg", hdr, info, tmp_path / "poster.jpg", 0, handle=handle)
        await derive.make_sprite("ffmpeg", hdr, info, tmp_path, handle)
        out = tmp_path / "hls"
        await hls.generate(
            "ffmpeg", "ffprobe", hdr, info, out, handle=handle, on_progress=lambda _: None
        )
        produced = await probe("ffprobe", str(out / "v0" / "index.m3u8"))
        assert produced.color_transfer == "bt709"
        assert produced.pix_fmt == "yuv420p"

    asyncio.run(scenario())


def test_hdr_repeated_source_labels_and_two_pass(hdr, tmp_path):
    async def scenario():
        info = await probe("ffprobe", str(hdr))
        source = (hdr, info)
        plans = [
            ops.plan_trim(
                ops.TrimParams(
                    segments=[ops.Segment(start=0, end=0.5), ops.Segment(start=1, end=1.5)]
                ),
                source,
                tmp_path / "trim.mp4",
            ),
            ops.plan_compress(
                ops.CompressParams(target_size_mb=0.1, preset="ultrafast"),
                source,
                tmp_path / "compress.mp4",
                tmp_path,
            ),
        ]
        for plan in plans:
            commands = sdr_commands(plan.commands, [source])
            for command in commands:
                await run_command(ffmpeg_args("ffmpeg", command), duration=plan.duration)
            path = tmp_path / ("trim.mp4" if plan is plans[0] else "compress.mp4")
            assert (await probe("ffprobe", str(path))).color_transfer == "bt709"
            await validate_output("ffmpeg", "ffprobe", path, expected_duration=plan.duration)

    asyncio.run(scenario())


def test_lossless_merge_requires_matching_colour_metadata(tmp_path):
    first = MediaInfo(video_codec="hevc", pix_fmt="yuv420p10le", color_transfer="smpte2084")
    second = MediaInfo(video_codec="hevc", pix_fmt="yuv420p10le", color_transfer="arib-std-b67")
    assert not ops.merge_lossless_ok([(tmp_path / "a.mp4", first), (tmp_path / "b.mp4", second)])


@pytest.mark.skipif(not os.environ.get("REELVAULT_TEST_HARDWARE"), reason="requires a real GPU")
@pytest.mark.parametrize("codec", ["h264", "h265"])
def test_real_hdr_hardware_encoding(hdr, tmp_path, codec):
    async def scenario():
        family = os.environ["REELVAULT_TEST_HARDWARE"]
        info = await probe("ffprobe", str(hdr))
        runtime = EncoderRuntime("ffmpeg", await detect_encoders("ffmpeg"))
        out = tmp_path / "hardware-sdr.mp4"
        plan = ops.plan_compress(ops.CompressParams(codec=codec), (hdr, info), out, tmp_path)
        result = await runtime.run(
            sdr_commands(plan.commands, [(hdr, info)]), family, duration=plan.duration
        )
        assert result["fallback"] is None, result
        assert family in result["encoder"]
        produced = await probe("ffprobe", str(out))
        assert produced.color_transfer == "bt709"
        await validate_output("ffmpeg", "ffprobe", out, expected_duration=2)
        await run_command(ffmpeg_args("ffmpeg", ["-xerror", "-i", str(out), "-f", "null", "-"]))

    asyncio.run(scenario())
