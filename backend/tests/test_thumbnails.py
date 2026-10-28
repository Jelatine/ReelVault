import asyncio
import json
import subprocess

import pytest

from reelvault.media import derive
from reelvault.media.derive import SPRITE, VTT, make_sprite, sprite_layout
from reelvault.media.probe import probe


@pytest.mark.parametrize(
    "duration,rate,gop,seeks",
    [(610, 1, 10000, False), (610, 1, 2, True), (3.2, 10, 10000, False), (0.2, 10, 10000, False)],
)
def test_sparse_long_and_subsecond_clips_have_complete_accurate_sprites(
    tmp_path, monkeypatch, duration, rate, gop, seeks
):
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
            str(gop),
            "-keyint_min",
            str(gop),
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
    keyframes = sum("K" in packet["flags"] for packet in packets)
    assert keyframes == 1 if gop == 10000 else keyframes > 1

    seeking = derive._sprite_by_seeking
    used: list[bool] = []

    async def spy(*args, **kwargs):
        used.append(True)
        await seeking(*args, **kwargs)

    monkeypatch.setattr(derive, "_sprite_by_seeking", spy)

    async def generate():
        info = await probe("ffprobe", str(source))
        await make_sprite("ffmpeg", source, info, tmp_path, ffprobe="ffprobe")
        return info

    info = asyncio.run(generate())
    # Sparse keyframes must keep the full decode; dense ones seek per thumbnail.
    assert bool(used) == seeks
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


@pytest.mark.parametrize("damaged_seconds", [(2, 3, 4, 5), tuple(range(6))])
def test_seeking_sprite_recovers_damaged_gops(tmp_path, damaged_seconds):
    from reelvault.media.ffmpeg import FFmpegError, ProcessHandle

    source = tmp_path / "damaged.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=red:size=160x90:rate=10:duration=6",
            "-c:v",
            "libx264",
            "-g",
            "10",
            "-keyint_min",
            "10",
            "-sc_threshold",
            "0",
            "-bf",
            "0",
            str(source),
        ],
        check=True,
    )
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
                "packet=pts_time,pos,size",
                "-of",
                "json",
                str(source),
            ]
        )
    )["packets"]
    # Preserve the MP4 index and codec configuration, but destroy complete GOPs.
    with source.open("r+b") as stream:
        for packet in packets:
            if int(float(packet["pts_time"])) in damaged_seconds:
                stream.seek(int(packet["pos"]))
                stream.write(b"\xff" * int(packet["size"]))
    logs = []
    progress = []

    async def generate():
        info = await probe("ffprobe", str(source))
        await derive._sprite_by_seeking(
            "ffmpeg",
            source,
            info,
            tmp_path / SPRITE,
            1,
            6,
            "scale=160:90,setsar=1",
            "tile=6x1",
            ProcessHandle(on_log=lambda message, level: logs.append((message, level))),
            progress.append,
        )

    if len(damaged_seconds) == 6:
        with pytest.raises(FFmpegError, match="所有采样位置"):
            asyncio.run(generate())
        assert not (tmp_path / SPRITE).exists()
    else:
        asyncio.run(generate())
        raw = subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(tmp_path / SPRITE),
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ]
        )
        assert len(raw) == 960 * 90 * 3
        for index in range(6):
            offset = (45 * 960 + index * 160 + 80) * 3
            red, green, blue = raw[offset : offset + 3]
            assert red > 200 and green < 40 and blue < 40
    assert any("将使用相邻有效帧" in message for message, _ in logs)
    assert progress == [(i + 1) / 6 for i in range(6)]
    assert not list(tmp_path.glob("tmp*"))


@pytest.mark.parametrize("error", [derive.FFmpegError("Permission denied"), RuntimeError("stop")])
def test_seeking_sprite_does_not_hide_operational_errors(tmp_path, monkeypatch, error):
    from reelvault.media.probe import MediaInfo

    async def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(derive, "run_command", fail)
    with pytest.raises(type(error), match=str(error)):
        asyncio.run(
            derive._sprite_by_seeking(
                "ffmpeg",
                tmp_path / "source.mp4",
                MediaInfo(duration=6),
                tmp_path / SPRITE,
                1,
                6,
                "scale=160:90",
                "tile=6x1",
                None,
                None,
            )
        )


@pytest.mark.parametrize("failure", ["decode", "empty", "cancel"])
def test_seeking_sprite_backfills_initial_frames(tmp_path, monkeypatch, failure):
    from reelvault.media.ffmpeg import Canceled, ProcessHandle
    from reelvault.media.probe import MediaInfo

    logs = []

    async def run(args, **kwargs):
        if args[-1].endswith(".png"):
            from pathlib import Path

            output = Path(args[-1])
            if output.name in {"0000.png", "0001.png", "0003.png"}:
                if failure == "decode":
                    output.write_bytes(b"partial")
                    raise derive.FFmpegError("Invalid NAL unit size")
                if failure == "cancel":
                    raise Canceled()
                return
            output.write_bytes(b"valid frame")
            return
        # Verify both initial gaps and later gaps are filled before tiling.
        from pathlib import Path

        frames = Path(args[args.index("-i") + 1]).parent
        assert [p.read_bytes() for p in sorted(frames.glob("*.png"))] == [b"valid frame"] * 6

    monkeypatch.setattr(derive, "run_command", run)

    async def generate():
        await derive._sprite_by_seeking(
            "ffmpeg",
            tmp_path / "source.mp4",
            MediaInfo(duration=6),
            tmp_path / SPRITE,
            1,
            6,
            "scale=160:90",
            "tile=6x1",
            ProcessHandle(on_log=lambda message, level: logs.append(message)),
            None,
        )

    if failure == "cancel":
        with pytest.raises(Canceled):
            asyncio.run(generate())
    else:
        asyncio.run(generate())
        assert sum("将使用相邻有效帧" in message for message in logs) == 3


def _damaged_video(path, duration, damaged_seconds, fill=b"\x00"):
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi",
            "-i", f"testsrc=size=160x90:rate=10:duration={duration}",
            "-c:v", "libx264", "-g", "10", "-keyint_min", "10", "-sc_threshold", "0",
            str(path),
        ],
        check=True,
    )  # fmt: skip
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
                "packet=pts_time,pos,size",
                "-of",
                "json",
                str(path),
            ]
        )  # fmt: skip
    )["packets"]
    # Zero-filled regions inside an intact MP4 index, as left by an interrupted upload.
    with path.open("r+b") as stream:
        for packet in packets:
            if int(float(packet["pts_time"])) in damaged_seconds:
                stream.seek(int(packet["pos"]))
                stream.write(fill * int(packet["size"]))
    return path


def test_preview_skips_segments_that_cannot_be_decoded(tmp_path):
    from reelvault.media.ffmpeg import ProcessHandle

    # Six 1 s segments start at 2, 7, 12, 17, 22 and 27 s; destroy the one at 22 s.
    source = _damaged_video(tmp_path / "damaged.mp4", 30, range(20, 25))
    out = tmp_path / derive.PREVIEW
    logs = []

    async def generate():
        info = await probe("ffprobe", str(source))
        await derive.make_preview(
            "ffmpeg",
            source,
            info,
            out,
            ProcessHandle(on_log=lambda message, level: logs.append((message, level))),
        )

    asyncio.run(generate())
    result = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(out)]
        )
    )
    assert 4.5 < float(result["format"]["duration"]) < 5.5
    assert [s["codec_type"] for s in result["streams"]] == ["video"]
    subprocess.run(
        ["ffmpeg", "-v", "error", "-xerror", "-i", str(out), "-f", "null", "-"], check=True
    )
    assert [level for message, level in logs if "预览片段 5/6" in message] == ["warning"]
    assert not list(tmp_path.glob("tmp*"))


def test_preview_fails_when_no_segment_decodes(tmp_path):
    from reelvault.media.ffmpeg import FFmpegError

    source = _damaged_video(tmp_path / "damaged.mp4", 30, range(1, 30))

    async def generate():
        info = await probe("ffprobe", str(source))
        await derive.make_preview("ffmpeg", source, info, tmp_path / derive.PREVIEW)

    with pytest.raises(FFmpegError, match="所有采样位置"):
        asyncio.run(generate())
    assert not list(tmp_path.glob("tmp*"))
