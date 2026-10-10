from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from reelvault.config import Settings
from reelvault.main import create_app
from reelvault.media import encoding, ops
from reelvault.media.encoding import EncoderRuntime, hardware_command
from reelvault.media.ffmpeg import Canceled, FFmpegError, RunResult
from reelvault.media.hardware_check import verify_operation
from reelvault.media.probe import probe

from .conftest import HEADERS, login


def test_hardware_filters_and_quality_options() -> None:
    args = ["-i", "input.mp4", "-vf", "transpose=clock", *ops.x264(20), "out.mp4"]
    for family, codec in [
        ("videotoolbox", "h264_videotoolbox"),
        ("qsv", "h264_qsv"),
        ("nvenc", "h264_nvenc"),
        ("vaapi", "h264_vaapi"),
    ]:
        changed = hardware_command(args, family, "/dev/dri/renderD129")
        assert changed[changed.index("-c:v") + 1] == codec
        assert "-crf" not in changed and "libx264" not in changed
        assert changed[-1] == "out.mp4"
        if family == "vaapi":
            assert changed[:2] == ["-vaapi_device", "/dev/dri/renderD129"]
            assert "transpose=clock,format=nv12,hwupload" in changed
        if family == "videotoolbox":
            assert changed[changed.index("-allow_sw") + 1] == "0"
    graph = [
        "-i",
        "in",
        "-filter_complex",
        "[0:v]null[v];[0:a]anull[a]",
        "-map",
        "[v]",
        "-map",
        "[a]",
        *ops.x264(22),
        "out.mp4",
    ]
    changed = hardware_command(graph, "vaapi", "/dev/dri/renderD128")
    assert changed[changed.index("-filter_complex") + 1].endswith(
        ";[v]format=nv12,hwupload[rv_hwv]"
    )
    assert changed[changed.index("-map") + 1] == "[rv_hwv]"
    assert "[a]" in changed
    preview = ["-filter_complex", "[0:v]null[out]", "-map", "[out]", *ops.x264(30), "preview.mp4"]
    assert (
        "[out]format=nv12,hwupload" in hardware_command(preview, "vaapi", "/dev/dri/renderD128")[3]
    )


def test_hardware_decode_and_bitrate_outputs() -> None:
    def output(label: str, name: str) -> list[str]:
        return [
            "-map", f"[{label}]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
            "-level:v", "4.0", "-refs", "3", "-b:v", "800000",
            "-force_key_frames", "expr:gte(t,n_forced*4)", name,
        ]  # fmt: skip

    args = ["-i", "in.mp4", "-filter_complex", "[0:v]split[v0][v1]", *output("v0", "a.m3u8")]
    args += output("v1", "b.m3u8")
    changed = hardware_command(args, "nvenc", "", decode=True)
    assert changed[:4] == ["-hwaccel", "cuda", "-i", "in.mp4"]
    assert changed.count("h264_nvenc") == 2 and changed.count("-forced-idr") == 2
    assert "-qp" not in changed and changed.count("vbr") == 2
    assert changed.index("-forced-idr") < changed.index("a.m3u8")
    assert "-refs" not in changed and changed.count("-level:v") == 2
    toolbox = hardware_command(args, "videotoolbox", "", decode=True)
    assert toolbox.count("-refs") == 2
    assert "-q:v" not in toolbox and toolbox[:2] == ["-hwaccel", "videotoolbox"]
    assert "-hwaccel" not in hardware_command(args, "videotoolbox", "")
    assert "-hwaccel" not in hardware_command(args, "qsv", "", decode=True)
    vaapi = hardware_command(args, "vaapi", "/dev/dri/renderD128", decode=True)
    assert vaapi[:6] == [
        "-vaapi_device", "/dev/dri/renderD128",
        "-hwaccel", "vaapi", "-hwaccel_device", "/dev/dri/renderD128",
    ]  # fmt: skip
    assert vaapi[vaapi.index("-filter_complex") + 1].endswith(
        ";[v0]format=nv12,hwupload[rv_hwv0];[v1]format=nv12,hwupload[rv_hwv1]"
    )
    maps = [vaapi[i + 1] for i, arg in enumerate(vaapi) if arg == "-map"]
    assert maps == ["[rv_hwv0]", "[a]", "[rv_hwv1]", "[a]"]


def test_entire_plan_retries_and_cancellation_does_not_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    async def run(args: list[str], **kwargs: Any) -> RunResult:
        calls.append(args)
        if "h264_nvenc" in args and args[-1] == "second.mp4":
            raise FFmpegError("GPU unavailable")
        return RunResult(b"", "")

    monkeypatch.setattr(encoding, "run_command", run)
    runtime = EncoderRuntime("ffmpeg", {"h264_nvenc"})
    commands = [["-i", "in.mp4", *ops.x264(20), name] for name in ("first.mp4", "second.mp4")]
    result = asyncio.run(runtime.run(commands, "nvenc"))
    assert [args[args.index("-c:v") + 1] for args in calls] == [
        "h264_nvenc",
        "h264_nvenc",
        "h264_nvenc",
        "h264_nvenc",
        "libx264",
        "libx264",
    ]
    assert [("-hwaccel" in args) for args in calls[:4]] == [True, True, False, False]
    assert result["fallback"] == "GPU unavailable" and result["encoder"] == "libx264"
    calls.clear()

    async def cancel(args: list[str], **kwargs: Any) -> RunResult:
        calls.append(args)
        raise Canceled()

    monkeypatch.setattr(encoding, "run_command", cancel)
    with pytest.raises(Canceled):
        asyncio.run(runtime.run(commands, "nvenc"))
    assert len(calls) == 1


def test_two_pass_and_stream_copy_stay_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def run(args: list[str], **kwargs: Any) -> RunResult:
        calls.append(args)
        return RunResult(b"", "")

    monkeypatch.setattr(encoding, "run_command", run)
    runtime = EncoderRuntime("ffmpeg", {"hevc_nvenc"})
    commands = [
        ["-c:v", "libx265", "-x265-params", "pass=1:stats=log", "-f", "null", "-"],
        ["-c:v", "libx265", "-x265-params", "pass=2:stats=log", "out.mp4"],
    ]
    result = asyncio.run(runtime.run(commands, "nvenc"))
    assert result["encoder"] == "libx265" and "两遍" in result["fallback"]
    assert all("hevc_nvenc" not in args for args in calls)
    commands = [["-i", "in.mp4", "-c", "copy", "out.mp4"]]
    result = asyncio.run(runtime.run(commands, "nvenc"))
    assert result["fallback"] is None and result["encoder"] == "copy/other"
    assert calls[-1][-len(commands[0]) :] == commands[0]


def test_auto_tries_next_compiled_hardware(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def run(args: list[str], **kwargs: Any) -> RunResult:
        calls.append(args)
        if "h264_qsv" in args:
            raise FFmpegError("Intel device unavailable")
        return RunResult(b"", "")

    monkeypatch.setattr(encoding, "run_command", run)
    runtime = EncoderRuntime("ffmpeg", {"h264_qsv", "h264_nvenc"})
    result = asyncio.run(runtime.run([[*ops.x264(20), "out.mp4"]], "auto"))
    assert result["encoder"] == "h264_nvenc" and result["fallback"] is None
    assert result["decoder"] == "cuda"
    assert len(calls) == 2 and "-hwaccel" not in calls[0]
    assert runtime.outcomes["h264_qsv"]["usable"] is False


def test_preference_persists_across_restart(settings: Settings) -> None:
    assert Settings.model_fields["encoder"].default == "auto"
    with TestClient(create_app(settings), headers=HEADERS) as client:
        assert client.get("/api/system/encoding").status_code == 401
        login(client)
        assert client.get("/api/system/encoding").json()["selected"] == "software"
        assert client.put("/api/system/encoding", json={"encoder": "invalid"}).status_code == 422
        client.app.state.jobs.encoding.compiled = set()  # type: ignore[attr-defined]
        assert client.put("/api/system/encoding", json={"encoder": "nvenc"}).status_code == 400
        assert (
            client.put("/api/system/encoding", json={"encoder": "auto"}).json()["selected"]
            == "auto"
        )
    settings.encoder = "software"
    with TestClient(create_app(settings), headers=HEADERS) as client:
        login(client)
        assert client.get("/api/system/encoding").json()["selected"] == "auto"


def test_real_software_fallback_output(
    samples: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = encoding.hardware_command

    def unavailable(args: list[str], family: str, device: str, **kwargs: Any) -> list[str]:
        hardware = original(args, family, device, **kwargs)
        hardware[-1:-1] = ["-gpu", "999"]
        return hardware

    monkeypatch.setattr(encoding, "hardware_command", unavailable)

    async def run() -> None:
        info = await probe("ffprobe", str(samples["a"]))
        out = tmp_path / "fallback.mp4"
        plan = ops.plan_rotate(ops.RotateParams(angle=90), (samples["a"], info), out)
        runtime = EncoderRuntime("ffmpeg", {"h264_nvenc"})
        result = await runtime.run(plan.commands, "nvenc", duration=plan.duration)
        assert result["encoder"] == "libx264" and result["fallback"]
        produced = await probe("ffprobe", str(out))
        assert (produced.width, produced.height, produced.video_codec) == (240, 320, "h264")
        assert produced.audio_codec == "aac" and 3.8 < produced.duration < 4.2

    asyncio.run(run())


@pytest.mark.skipif(
    not os.environ.get("REELVAULT_TEST_HARDWARE"),
    reason="requires actual GPU/driver; opt in explicitly",
)
@pytest.mark.parametrize("operation", ["h264", "h265", "rotate", "merge"])
def test_real_hardware_output(samples: dict[str, Path], tmp_path: Path, operation: str) -> None:
    family = os.environ["REELVAULT_TEST_HARDWARE"]
    assert family in encoding.FAMILIES, f"Unknown hardware family: {family}"

    asyncio.run(
        verify_operation(
            family,
            operation,
            samples,
            tmp_path,
            device=os.environ.get("REELVAULT_TEST_VAAPI_DEVICE", "/dev/dri/renderD128"),
        )
    )
