from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from reelvault.media import ffmpeg
from reelvault.media.ffmpeg import FFmpegCommand, FFmpegError, ProcessHandle, run_command


@pytest.mark.parametrize(
    ("detail", "error_type"),
    [
        ("No space left on device", ffmpeg.FFmpegOperationalError),
        ("No such filter: zscale", ffmpeg.FFmpegOperationalError),
        ("Failed to open /dev/dri/renderD128 device: Permission denied", FFmpegError),
    ],
)
def test_failure_diagnostics_keep_gpu_fallback_available(detail, error_type):
    with pytest.raises(error_type) as failure:
        asyncio.run(
            run_command(
                [
                    sys.executable,
                    "-c",
                    f"import sys; sys.stderr.write({detail!r}); sys.exit(1)",
                ]
            )
        )
    assert type(failure.value) is error_type
    assert detail in str(failure.value)


def test_timeout_reaps_process_and_keeps_handle_reusable():
    async def scenario():
        handle = ProcessHandle()
        with pytest.raises(ffmpeg.FFmpegTimeout, match="超过"):
            await run_command(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                handle=handle,
                timeout=0.1,
            )
        assert handle.process is None
        assert not handle.canceled
        result = await run_command([sys.executable, "-c", "print('ok')"], handle=handle)
        assert result.stdout.strip() == b"ok"

    asyncio.run(scenario())


def test_repeated_progress_heartbeat_does_not_hide_stall():
    script = (
        "import time\n"
        "while True:\n"
        " print('out_time_us=0\\nframe=0\\nprogress=continue', flush=True)\n"
        " time.sleep(0.02)\n"
    )
    handle = ProcessHandle(stall_timeout=0.15)
    with pytest.raises(ffmpeg.FFmpegTimeout, match="无处理进展"):
        asyncio.run(
            run_command(
                FFmpegCommand([sys.executable, "-c", script, "-progress", "pipe:1"]),
                handle=handle,
            )
        )
    assert handle.process is None


def test_single_frame_analysis_does_not_trigger_encoding_stall_timeout():
    handle = ProcessHandle(stall_timeout=0.05)
    result = asyncio.run(
        run_command(
            FFmpegCommand(
                [
                    sys.executable,
                    "-c",
                    "import time; time.sleep(0.2); print('frame=1')",
                    "-progress",
                    "pipe:1",
                    "-frames:v",
                    "1",
                ]
            ),
            handle=handle,
            timeout=2,
        )
    )
    assert result.extra["progress"]["frame"] == 1


def test_timeout_excludes_paused_time():
    async def scenario():
        handle = ProcessHandle(stall_timeout=0)
        task = asyncio.create_task(
            run_command(
                [sys.executable, "-c", "import time; time.sleep(0.25); print('ok')"],
                handle=handle,
                timeout=0.5,
            )
        )
        while handle.process is None:
            await asyncio.sleep(0.001)
        handle.pause()
        await asyncio.sleep(0.6)
        handle.resume()
        assert (await task).stdout.strip() == b"ok"

    asyncio.run(scenario())


def test_thread_budget_is_applied_to_real_encoder_and_filters(monkeypatch, tmp_path):
    calls = []
    start = ffmpeg.start_process

    async def capture(args, handle, **kwargs):
        calls.append(args)
        return await start(args, handle, **kwargs)

    monkeypatch.setattr(ffmpeg, "start_process", capture)
    asyncio.run(
        run_command(
            ffmpeg.ffmpeg_args(
                "ffmpeg",
                [
                    "-f",
                    "lavfi",
                    "-i",
                    "color=s=64x64:d=0.2",
                    "-c:v",
                    "libx265",
                    "-x265-params",
                    "log-level=error",
                    str(tmp_path / "limited.mp4"),
                ],
            ),
            handle=ProcessHandle(threads=1),
        )
    )
    args = calls[0]
    assert args[args.index("-filter_threads") + 1] == "1"
    assert args[args.index("-filter_complex_threads") + 1] == "1"
    assert args[args.index("-threads:v") + 1] == "1"
    assert "log-level=error:pools=1:frame-threads=1" in args


def test_operation_summary_includes_filters_and_redacts_source_locations():
    summary = ffmpeg._operation_summary(
        [
            "ffmpeg",
            "-i",
            "/private/library/source video.mp4",
            "-i",
            "https://user:password@example.com/video?token=secret",
            "-c:v",
            "libx264",
            "-vf",
            "scale=640:-2",
            "-pass",
            "2",
            "/private/tmp/output.mp4",
        ]
    )
    assert "-i 'source video.mp4'" in summary
    assert "-c:v libx264 -vf scale=640:-2 -pass 2" in summary
    assert summary.endswith("→ output.mp4")
    assert "[URL]" in summary
    assert not any(secret in summary for secret in ("/private", "password", "token=secret"))


def test_progress_logs_are_throttled_and_stdout_is_preserved(monkeypatch):
    # Four complete FFmpeg progress blocks: the second is inside the 10s window.
    blocks = [
        f"out_time_us={n * 1000000}\nout_time=00:00:0{n}.000000\n"
        f"frame={n * 25}\nfps=25.0\nspeed=2.0x\nbitrate=100kbits/s\n"
        f"total_size=1048576\nprogress={'end' if n == 4 else 'continue'}\n"
        for n in range(1, 5)
    ]
    # Emit exact bytes on every platform, including Windows text-mode CRLF hosts.
    script = f"import sys;sys.stdout.buffer.write({''.join(blocks).encode()!r})"
    events = []
    ticks = iter([0, 0, 1, 11, 12, 13])
    monkeypatch.setattr(ffmpeg, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    handle = ProcessHandle(on_log=lambda message, level: events.append((message, level)))
    fractions = []
    result = asyncio.run(
        run_command(
            FFmpegCommand([sys.executable, "-c", script, "-progress", "pipe:1"]),
            handle=handle,
            on_progress=fractions.append,
        )
    )
    assert result.stdout.decode() == "".join(blocks)
    assert fractions == []  # Unknown duration still logs statistics without dividing by zero.
    progress = [message for message, _ in events if message.startswith("FFmpeg 进度：")]
    assert len(progress) == 3
    assert "frame=25" in progress[0] and "frame=75" in progress[1]
    assert "speed=2.0x" in progress[0] and "size=1.00 MiB" in progress[0]
    assert events[0][0].startswith("FFmpeg 开始：")
    assert events[-1] == ("FFmpeg 完成（耗时 13.0 秒）", "info")


def test_failure_and_cancellation_do_not_log_completion():
    events = []
    handle = ProcessHandle(on_log=lambda message, level: events.append((message, level)))
    with pytest.raises(FFmpegError, match="bad codec"):
        asyncio.run(
            run_command(
                FFmpegCommand(
                    [sys.executable, "-c", 'import sys;sys.stderr.write("bad codec");sys.exit(2)']
                ),
                handle=handle,
            )
        )
    assert events[-1] == ("FFmpeg 执行失败（退出码 2）：bad codec", "warning")
    assert not any("FFmpeg 完成" in message for message, _ in events)
    events.clear()
    handle.cancel()
    with pytest.raises(ffmpeg.Canceled):
        asyncio.run(run_command(FFmpegCommand([sys.executable]), handle=handle))
    assert events == []


def test_only_marked_commands_get_ffmpeg_handling():
    script = "print('ok')"
    events = []
    handle = ProcessHandle(on_log=lambda message, level: events.append(message))
    # Flags alone no longer make a command an FFmpeg run: no job log, no stall detection.
    asyncio.run(
        run_command([sys.executable, "-c", script, "-hide_banner", "-nostdin"], handle=handle)
    )
    assert events == []
    marked = FFmpegCommand([sys.executable, "-c", script])
    asyncio.run(run_command(marked, handle=handle))
    assert events[0].startswith("FFmpeg 开始：") and events[-1].startswith("FFmpeg 完成")
    built = ffmpeg.ffmpeg_args("ffmpeg", ["-i", "x"], loglevel="info")
    assert isinstance(built, FFmpegCommand)
    assert built[:6] == ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "info"]
