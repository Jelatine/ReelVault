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
from reelvault.media.ffmpeg import (
    Canceled,
    FFmpegError,
    FFmpegOperationalError,
    FFmpegTimeout,
    ProcessHandle,
    RunResult,
)
from reelvault.media.hardware_check import verify_operation
from reelvault.media.probe import probe

from .conftest import HEADERS, login


@pytest.mark.parametrize("stats", [None, b"", b"first-pass statistics"])
def test_av1_runtime_stops_before_second_pass_without_stats(monkeypatch, tmp_path, stats):
    calls = []

    async def run(args, **kwargs):
        calls.append(args)
        if args[args.index("-pass") + 1] == "1" and stats is not None:
            (tmp_path / "stats-0.log").write_bytes(stats)

    monkeypatch.setattr(encoding, "run_command", run)
    commands = [["-c:v", "libsvtav1", "-pass", str(n), "-passlogfile", "stats"] for n in (1, 2)]
    runtime = EncoderRuntime("ffmpeg")
    task = runtime.run(commands, "auto", cwd=tmp_path)
    if stats:
        result = asyncio.run(task)
        assert result["encoder"] == "libsvtav1"
        assert len(calls) == 2
    else:
        with pytest.raises(FFmpegOperationalError, match="AV1"):
            asyncio.run(task)
        assert len(calls) == 1


@pytest.mark.parametrize("failure", [FFmpegTimeout("stalled"), FFmpegOperationalError("disk full")])
def test_operational_errors_do_not_retry_other_encoders(monkeypatch, failure):
    calls = []

    async def fail(args, **kwargs):
        calls.append(args)
        raise failure

    monkeypatch.setattr(encoding, "run_command", fail)
    runtime = EncoderRuntime("ffmpeg", {"h264_nvenc"})
    with pytest.raises(type(failure)):
        asyncio.run(runtime.run([["-i", "in.mp4", *ops.x264(20), "out.mp4"]], "nvenc"))
    assert len(calls) == 1


def test_gpu_wait_is_cancellable_and_releases_slots():
    async def scenario():
        runtime = EncoderRuntime("ffmpeg", max_hardware_jobs=1)
        handle = ProcessHandle()
        async with runtime.hardware_slot(None):

            async def waiting():
                async with runtime.hardware_slot(handle):
                    pytest.fail("a second GPU job acquired the occupied slot")

            task = asyncio.create_task(waiting())
            await asyncio.sleep(0.05)
            assert not task.done()
            handle.cancel()
            with pytest.raises(Canceled):
                await asyncio.wait_for(task, 1)
        # A cancelled waiter must neither leak a slot nor increase the capacity.
        async with runtime.hardware_slot(None):
            assert runtime._hardware_slots.locked()

    asyncio.run(scenario())


def test_piece_progress_uses_each_steps_duration(monkeypatch):
    durations = []
    progress = []

    async def advance(args, *, duration, on_progress, **kwargs):
        durations.append(duration)
        on_progress(0.5)
        return RunResult(b"", "")

    monkeypatch.setattr(encoding, "run_command", advance)
    runtime = EncoderRuntime("ffmpeg")
    commands = [
        ["-i", "source.mp4", "-t", str(seconds), "-c:v", "libx264", "out.mp4"] for seconds in (1, 9)
    ]
    asyncio.run(runtime.run(commands, "software", duration=10, on_progress=progress.append))
    assert durations == [1, 9]
    assert progress == pytest.approx([0.05, 0.1, 0.55, 1])


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
    events = []
    handle = ProcessHandle(on_log=lambda message, level: events.append((message, level)))
    result = asyncio.run(runtime.run(commands, "nvenc", handle=handle))
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
    assert [message for message, _ in events if message.startswith("FFmpeg 执行步骤")] == [
        "FFmpeg 执行步骤 1/2",
        "FFmpeg 执行步骤 2/2",
        "FFmpeg 执行步骤 1/2",
        "FFmpeg 执行步骤 2/2",
        "FFmpeg 执行步骤 1/2",
        "FFmpeg 执行步骤 2/2",
    ]
    assert ("使用软件编码：GPU unavailable", "warning") in events
    assert sum(message.startswith("硬件处理失败") for message, _ in events) == 2
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


def test_startup_check_skips_absent_devices_in_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def run(args: list[str], **kwargs: Any) -> RunResult:
        calls.append(args)
        if "h264_qsv" in args or "hevc_qsv" in args:
            raise FFmpegError("MFX session: -9")
        return RunResult(b"", "")

    monkeypatch.setattr(encoding, "run_command", run)
    runtime = EncoderRuntime("ffmpeg", {"h264_qsv", "hevc_qsv", "h264_nvenc"})
    asyncio.run(runtime.verify())
    assert len(calls) == 3 and all("lavfi" in args and "-hwaccel" not in args for args in calls)
    assert runtime.unavailable == {"h264_qsv", "hevc_qsv"} and not runtime.probing
    assert runtime.outcomes["h264_nvenc"]["usable"] is True
    assert "MFX" in runtime.outcomes["h264_qsv"]["error"]
    calls.clear()
    result = asyncio.run(runtime.run([[*ops.x264(20), "out.mp4"]], "auto"))
    assert result["encoder"] == "h264_nvenc" and len(calls) == 1
    # An explicit choice still tries the device, and a success makes it eligible again.
    monkeypatch.setattr(encoding, "run_command", lambda args, **kwargs: asyncio.sleep(0))
    result = asyncio.run(runtime.run([[*ops.x264(20), "out.mp4"]], "qsv"))
    assert result["encoder"] == "h264_qsv" and "h264_qsv" not in runtime.unavailable


def test_hardware_av1_from_compress_plan(tmp_path: Path) -> None:
    info = ops.MediaInfo(width=1280, height=720, duration=10, fps=30, video_codec="hevc")
    info.pix_fmt = "yuv420p10le"
    params = ops.CompressParams(codec="av1", quality="high")
    (args,) = ops.plan_compress(
        params, (tmp_path / "in", info), tmp_path / "o.mp4", tmp_path
    ).commands
    expected = {"qsv": "av1_qsv", "nvenc": "av1_nvenc", "vaapi": "av1_vaapi"}
    for family, codec in expected.items():
        changed = hardware_command(args, family, "/dev/dri/renderD128")
        assert changed[changed.index("-c:v") + 1] == codec
        assert "libsvtav1" not in changed and "-crf" not in changed
        assert "-profile:v" not in changed  # AV1 Main covers 10 bits
    qsv = hardware_command(args, "qsv", "")
    assert qsv[qsv.index("-global_quality") + 1] == "120"  # CRF 30 on the 0-255 scale
    assert qsv[qsv.index("-pix_fmt") + 1] == "p010le"
    nvenc = hardware_command(args, "nvenc", "")
    assert nvenc[nvenc.index("-qp") + 1] == "120"
    vaapi = hardware_command(args, "vaapi", "/dev/dri/renderD128")
    assert vaapi[vaapi.index("-vf") + 1].endswith("format=p010,hwupload")
    # VideoToolbox has no AV1 encoder: the command stays software.
    assert hardware_command(args, "videotoolbox", "") == args


def test_hardware_av1_choices_and_status() -> None:
    runtime = EncoderRuntime("ffmpeg", {"h264_videotoolbox", "av1_nvenc", "av1_qsv"})
    runtime.unavailable = {"av1_qsv"}
    assert runtime.choices("auto", "libsvtav1") == ["nvenc"]
    assert runtime.choices("videotoolbox", "libsvtav1") == []
    assert runtime.choices("qsv", "libsvtav1") == ["qsv"]
    families = {f["value"]: f for f in runtime.status("auto")["families"]}
    assert [e["name"] for e in families["videotoolbox"]["encoders"]] == [
        "h264_videotoolbox",
        "hevc_videotoolbox",
    ]
    assert [e["name"] for e in families["nvenc"]["encoders"]][-1] == "av1_nvenc"


def test_hardware_av1_run_and_two_pass_stays_software(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    async def run(args: list[str], **kwargs: Any) -> None:
        calls.append(args)

    monkeypatch.setattr(encoding, "run_command", run)
    runtime = EncoderRuntime("ffmpeg", {"av1_nvenc"})
    command = ["-i", "in.mp4", "-c:v", "libsvtav1", "-preset", "8", "-crf", "36", "out.mp4"]
    result = asyncio.run(runtime.run([command], "auto"))
    assert result["encoder"] == "av1_nvenc" and "av1_nvenc" in calls[-1]
    two_pass = [[*command[:-1], "-b:v", "500k", "-pass", "1", "-f", "null", "-"]]
    monkeypatch.setattr(encoding.Path, "is_file", lambda self: True)
    monkeypatch.setattr(encoding.Path, "stat", lambda self: type("S", (), {"st_size": 1})())
    two_pass[0][-3:-3] = ["-passlogfile", "log"]
    result = asyncio.run(runtime.run(two_pass, "auto"))
    assert result["encoder"] == "libsvtav1" and result["fallback"]


def test_vaapi_unsupported_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(encoding.sys, "platform", "win32")
    runtime = EncoderRuntime("ffmpeg", {"h264_vaapi", "h264_nvenc"})
    assert runtime.choices("auto", "libx264") == ["nvenc"]
    assert runtime.choices("vaapi", "libx264") == []
    families = {f["value"]: f["supported"] for f in runtime.status("auto")["families"]}
    assert families == {"videotoolbox": True, "qsv": True, "vaapi": False, "nvenc": True}


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
@pytest.mark.parametrize("operation", ["h264", "h265", "av1", "rotate", "merge"])
def test_real_hardware_output(samples: dict[str, Path], tmp_path: Path, operation: str) -> None:
    family = os.environ["REELVAULT_TEST_HARDWARE"]
    assert family in encoding.FAMILIES, f"Unknown hardware family: {family}"
    if operation == "av1" and not os.environ.get("REELVAULT_TEST_HARDWARE_AV1"):
        pytest.skip("hardware AV1 encoding is opt-in: set REELVAULT_TEST_HARDWARE_AV1=1")

    asyncio.run(
        verify_operation(
            family,
            operation,
            samples,
            tmp_path,
            device=os.environ.get("REELVAULT_TEST_VAAPI_DEVICE", "/dev/dri/renderD128"),
        )
    )
