from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from reelvault.media import ffmpeg
from reelvault.media.ffmpeg import FFmpegError, ProcessHandle, run_command


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
    script = f"import sys;sys.stdout.write({''.join(blocks)!r})"
    events = []
    ticks = iter([0, 0, 1, 11, 12, 13])
    monkeypatch.setattr(ffmpeg, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    handle = ProcessHandle(on_log=lambda message, level: events.append((message, level)))
    fractions = []
    result = asyncio.run(
        run_command(
            [sys.executable, "-c", script, "-hide_banner", "-progress", "pipe:1"],
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
                [
                    sys.executable,
                    "-c",
                    'import sys;sys.stderr.write("bad codec");sys.exit(2)',
                    "-hide_banner",
                ],
                handle=handle,
            )
        )
    assert events[-1] == ("FFmpeg 执行失败（退出码 2）：bad codec", "warning")
    assert not any("FFmpeg 完成" in message for message, _ in events)
    events.clear()
    handle.cancel()
    with pytest.raises(ffmpeg.Canceled):
        asyncio.run(run_command([sys.executable, "-hide_banner"], handle=handle))
    assert events == []
