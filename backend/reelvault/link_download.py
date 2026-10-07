"""Optional yt-dlp subprocess with bounded output, disk use and active running time."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import math
import os
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import Any

from .config import Settings
from .jobs.manager import JobContext
from .link_network import DownloadProxy
from .media.ffmpeg import Canceled
from .storage import MIB, check_budget, link_requirements


def downloader_command(settings: Settings) -> list[str] | None:
    if settings.yt_dlp == "yt-dlp" and importlib.util.find_spec("yt_dlp"):
        return [sys.executable, "-m", "yt_dlp"]
    executable = shutil.which(settings.yt_dlp)
    return [executable] if executable else None


def arguments(
    command: list[str], url: str, work: Path, proxy: str, limit: int, ffmpeg: str
) -> list[str]:
    protocols = "[protocol~='^(https?|m3u8_native|http_dash_segments)$']"
    runtime = shutil.which("deno")
    js_args = ["--no-js-runtimes"]
    if runtime:
        js_args += ["--js-runtimes", f"deno:{runtime}"]
    return [
        *command,
        "--ignore-config",
        "--no-plugin-dirs",
        "--no-remote-components",
        "--no-playlist",
        "--playlist-items",
        "1",
        "--match-filters",
        "!is_live",
        *js_args,
        "--no-update",
        "--no-cache-dir",
        "--no-warnings",
        "--proxy",
        proxy,
        "--socket-timeout",
        "30",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--hls-prefer-native",
        "--downloader",
        "native",
        "--max-filesize",
        str(limit),
        "--ffmpeg-location",
        shutil.which(ffmpeg) or ffmpeg,
        "--merge-output-format",
        "mkv",
        "-f",
        f"bv*{protocols}+ba{protocols}/b{protocols}",
        "--output",
        str(work / "video.%(ext)s"),
        "--newline",
        "--progress",
        "--progress-template",
        'download:RV_PROGRESS:{"done":%(progress.downloaded_bytes)j,"total":%(progress.total_bytes)j,"estimate":%(progress.total_bytes_estimate)j}',
        "--print",
        'after_move:RV_RESULT:{"path":%(filepath)j,"title":%(title)j}',
        "--output-na-placeholder",
        "null",
        "--",
        url,
    ]


def number(value: Any) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else 0
    except (ValueError, TypeError):
        return 0


async def download(ctx: JobContext, params: dict[str, Any], work: Path) -> tuple[Path, str]:
    settings = ctx.settings
    command = downloader_command(settings)
    if not command:
        raise RuntimeError("未安装 yt-dlp，无法导入链接")
    limit = int(params["limit_bytes"])
    await ctx.handle.checkpoint()
    async with DownloadProxy(
        limit * 4 + 16 * MIB, allow_private=settings.link_import_allow_private
    ) as proxy:
        env = dict(os.environ)
        for name in (
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
        ):
            env[name] = proxy.url
        env["no_proxy"] = env["NO_PROXY"] = ""
        env["DENO_DIR"] = str(work / ".deno")
        args = arguments(command, params["url"], work, proxy.url, limit, settings.ffmpeg)
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=work,
            start_new_session=True,
            limit=65536,
        )
        ctx.handle.process = process
        ctx.handle.process_group = True
        if ctx.handle.canceled:
            ctx.handle.cancel()
        elif ctx.handle.paused:
            ctx.handle.pause()
        result: dict[str, Any] = {}
        errors = bytearray()

        async def output() -> None:
            assert process.stdout is not None
            while line := await process.stdout.readline():
                if line.startswith(b"RV_PROGRESS:"):
                    progress = json.loads(line[12:])
                    total = number(progress.get("total")) or number(progress.get("estimate"))
                    ctx.set_progress(
                        min(0.8, number(progress.get("done")) / total * 0.8) if total else 0,
                        "下载链接视频",
                    )
                elif line.startswith(b"RV_RESULT:"):
                    result.update(json.loads(line[10:]))

        async def stderr() -> None:
            assert process.stderr is not None
            while chunk := await process.stderr.read(4096):
                errors.extend(chunk)
                del errors[:-32768]

        async def monitor() -> None:
            active = 0.0
            previous = time.monotonic()
            while process.returncode is None:
                await asyncio.sleep(0.5)
                current = time.monotonic()
                if not ctx.handle.paused:
                    active += current - previous
                previous = current
                if active > int(params["timeout_minutes"]) * 60:
                    raise RuntimeError("链接下载超时")
                written = 0
                for path in work.rglob("*"):
                    # Native fragment downloaders rename/remove files while being sampled.
                    with contextlib.suppress(FileNotFoundError):
                        if path.is_file():
                            written += path.stat().st_size
                if written > limit * 4 + 16 * MIB:
                    raise RuntimeError("下载临时文件超过限制")
                plan = link_requirements(limit, params["storage_id"])
                plan["local"] = max(0, plan["local"] - written)
                with ctx.db() as db:
                    check_budget(
                        db, settings, sum(plan.values()), exclude_job=ctx.job_id, requirements=plan
                    )

        tasks = [
            asyncio.create_task(output()),
            asyncio.create_task(stderr()),
            asyncio.create_task(monitor()),
        ]
        waiter = asyncio.create_task(process.wait())
        try:
            done, _ = await asyncio.wait([*tasks, waiter], return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                task.result()
            await waiter
            await tasks[0]
            await tasks[1]
            ctx.check_canceled()
            if process.returncode != 0 or not result.get("path"):
                detail = errors.decode("utf-8", errors="replace").strip().splitlines()
                message = proxy.reason or (detail[-1] if detail else "下载源没有提供可导入的视频")
                raise RuntimeError(
                    message.replace(proxy.url, "[proxy]").replace(str(work), "[temporary]")[:2000]
                )
            path = Path(result["path"])
            if path.parent.resolve() != work.resolve() or path.is_symlink() or not path.is_file():
                raise RuntimeError("下载文件路径无效")
            if not 0 < path.stat().st_size <= limit:
                raise RuntimeError("下载视频超过大小限制或内容为空")
            return path, str(result.get("title") or "video")[:255]
        except asyncio.CancelledError:
            raise
        except Exception:
            if ctx.handle.canceled:
                raise Canceled() from None
            raise
        finally:
            # Kill descendants too: yt-dlp may have started a local ffmpeg merger.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
            for task in [*tasks, waiter]:
                task.cancel()
            await asyncio.gather(*tasks, waiter, return_exceptions=True)
            ctx.handle.process = None
            ctx.handle.process_group = False
