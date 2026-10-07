import asyncio
import json
import subprocess

import pytest

from reelvault.media.derive import SPRITE, VTT, make_sprite, sprite_layout
from reelvault.media.probe import probe


@pytest.mark.parametrize("duration,rate", [(610, 1), (3.2, 10), (0.2, 10)])
def test_sparse_long_and_subsecond_clips_have_complete_accurate_sprites(tmp_path, duration, rate):
    source = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=red:size=160x90:rate={rate}:duration={duration}",
            "-vf",
            f"drawbox=color=blue:t=fill:enable='gte(t,{duration / 2})'",
            "-c:v",
            "libx264",
            "-g",
            "10000",
            "-keyint_min",
            "10000",
            "-sc_threshold",
            "0",
            "-bf",
            "0",
            str(source),
        ],
        check=True,
    )
    # The long fixture changes color without a second keyframe, reproducing the
    # previous missing sprite and checking content rather than command spelling.
    packets = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_packets",
                "-show_entries",
                "packet=flags",
                "-of",
                "json",
                str(source),
            ]
        )
    )["packets"]
    assert sum("K" in packet["flags"] for packet in packets) == 1

    async def generate():
        info = await probe("ffprobe", str(source))
        await make_sprite("ffmpeg", source, info, tmp_path)
        return info

    info = asyncio.run(generate())
    interval, count = sprite_layout(info.duration)
    sprite = tmp_path / SPRITE
    assert sprite.is_file() and sprite.stat().st_size > 0
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(sprite),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ]
    )
    columns = min(count, 10)
    width = columns * 160
    assert len(raw) == width * ((count + columns - 1) // columns) * 90 * 3
    for index in range(count):
        offset = ((index // columns * 90 + 45) * width + index % columns * 160 + 80) * 3
        red, green, blue = raw[offset : offset + 3]
        assert green < 40 and max(red, blue) > 200
        # Avoid the one-frame rounding boundary; all other cells must represent
        # the requested interval, including the final visible thumbnail.
        position = index * interval
        if position < duration / 2 - 1 / rate:
            assert red > 200 and blue < 40
        elif position > duration / 2 + 1 / rate:
            assert blue > 200 and red < 40
    vtt = (tmp_path / VTT).read_text()
    assert vtt.count("#xywh=") == count
    assert vtt.startswith("WEBVTT")
    end = {610: "00:10:10.000", 3.2: "00:00:03.200", 0.2: "00:00:00.200"}[duration]
    assert f"--> {end}" in vtt
