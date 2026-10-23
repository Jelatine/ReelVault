"""Hardware encoding of existing software-filter plans, with whole-plan fallback."""

from __future__ import annotations

import asyncio
import math
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ffmpeg import (
    FFmpegError,
    FFmpegOperationalError,
    ProcessHandle,
    ProgressCallback,
    ffmpeg_args,
    run_command,
)

FAMILIES = {
    "videotoolbox": ("VideoToolbox (macOS)", "h264_videotoolbox", "hevc_videotoolbox"),
    "qsv": ("Intel QSV", "h264_qsv", "hevc_qsv"),
    "vaapi": ("VAAPI (Linux)", "h264_vaapi", "hevc_vaapi"),
    "nvenc": ("NVIDIA NVENC", "h264_nvenc", "hevc_nvenc"),
}
# Windows FFmpeg builds compile VAAPI through libva-win32, but there is no render device.
UNSUPPORTED = {"win32": {"vaapi"}}
PROBE_TIMEOUT = 30


def supported(family: str) -> bool:
    return family not in UNSUPPORTED.get(sys.platform, set())


async def detect_encoders(ffmpeg: str) -> set[str]:
    try:
        result = await run_command([ffmpeg, "-hide_banner", "-encoders"], timeout=30)
    except (FFmpegError, OSError):
        return set()
    return set(re.findall(r"^\s*V\S{5}\s+(\S+)", result.stdout.decode(errors="replace"), re.M))


def software_encoder(args: list[str]) -> str | None:
    for i, arg in enumerate(args[:-1]):
        if arg == "-c:v" and args[i + 1] in {"libx264", "libx265"}:
            return args[i + 1]
    return None


# Hardware decoders paired with each encoder family. Unsupported codecs (images, lavfi)
# silently decode in software; a missing device fails, so callers retry without it.
DECODERS = {"videotoolbox": "videotoolbox", "nvenc": "cuda", "vaapi": "vaapi"}


def hardware_command(
    args: list[str], family: str, device: str, *, decode: bool = False
) -> list[str]:
    """Swap software encoders for ``family``; supports multiple outputs per command.

    Each output must map its video before its other streams, as VAAPI uploads the
    first ``-map [label]`` following the previous output's ``-c:v``.
    """
    software = software_encoder(args)
    if not software:
        return list(args)
    encoder = FAMILIES[family][1 if software == "libx264" else 2]
    crf = int(args[args.index("-crf") + 1]) if "-crf" in args else 23
    bitrate = "-b:v" in args
    # 10-bit output is only requested for HEVC; every family encodes it as Main10 P010.
    ten_bit = software == "libx265" and any(
        args[i] == "-pix_fmt" and args[i + 1] == "yuv420p10le" for i in range(len(args) - 1)
    )
    main10 = ["-profile:v", "main10"] if ten_bit else []
    if family == "videotoolbox":
        quality = max(1, min(100, round(100 - crf * 100 / 51)))
        rate = [] if bitrate else ["-q:v", str(quality)]
        options = ["-allow_sw", "0", *rate, "-pix_fmt", "p010le" if ten_bit else "yuv420p"]
    elif family == "qsv":
        rate = [] if bitrate else ["-global_quality", str(max(1, crf))]
        options = [*rate, "-preset", "medium", "-pix_fmt", "p010le" if ten_bit else "nv12"]
        if "-force_key_frames" in args:
            options += ["-forced_idr", "1"]
    elif family == "nvenc":
        rate = ["-rc", "vbr"] if bitrate else ["-rc", "constqp", "-qp", str(crf)]
        options = [*rate, "-preset", "p4", "-pix_fmt", "p010le" if ten_bit else "yuv420p"]
        if "-force_key_frames" in args:
            options += ["-forced-idr", "1"]
    else:
        options = ["-rc_mode", "VBR"] if bitrate else ["-rc_mode", "CQP", "-qp", str(crf)]
    options += main10
    dropped = {"-preset", "-crf", "-pix_fmt"}
    if family == "nvenc":
        # NVENC counts its B-frames against the level's DPB, so -refs 3 with -level 4.0
        # is rejected at 1080p ("Invalid Level"); the encoder picks refs within the level.
        dropped.add("-refs")
    result = []
    i = 0
    while i < len(args):
        if args[i] in dropped:
            i += 2
        elif args[i] == "-c:v":
            result.extend(["-c:v", encoder, *options])
            i += 2
        elif args[i] == "-i" and decode and family in DECODERS:
            result.extend(["-hwaccel", DECODERS[family]])
            if family == "vaapi":
                result.extend(["-hwaccel_device", device])
            result.extend(args[i : i + 2])
            i += 2
        else:
            result.append(args[i])
            i += 1
    if family == "vaapi":
        result = ["-vaapi_device", device, *result]
        upload = f"format={'p010' if ten_bit else 'nv12'},hwupload"
        if "-filter_complex" in result:
            pos = result.index("-filter_complex") + 1
            pending = True
            for j in range(len(result) - 1):
                if result[j] == "-c:v":
                    pending = True
                elif pending and result[j] == "-map" and result[j + 1].startswith("["):
                    label = result[j + 1]
                    hw = f"[rv_hw{label[1:]}"
                    result[pos] += f";{label}{upload}{hw}"
                    result[j + 1] = hw
                    pending = False
        else:
            flag = "-vf" if "-vf" in result else "-filter:v" if "-filter:v" in result else None
            if flag:
                pos = result.index(flag) + 1
                result[pos] += "," + upload
            else:
                result[-1:-1] = ["-vf", upload]
    return result


@dataclass
class EncoderRuntime:
    ffmpeg: str
    compiled: set[str] = field(default_factory=set)
    device: str = "/dev/dri/renderD128"
    outcomes: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Encoders whose startup check failed; auto mode skips them until one succeeds.
    unavailable: set[str] = field(default_factory=set)
    probing: bool = False
    max_hardware_jobs: int = 2
    _hardware_slots: asyncio.Semaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._hardware_slots = asyncio.Semaphore(self.max_hardware_jobs)

    @asynccontextmanager
    async def hardware_slot(self, handle: ProcessHandle | None) -> AsyncIterator[None]:
        """A waiting job remains cancellable even if all GPU slots are occupied."""
        pending = asyncio.create_task(self._hardware_slots.acquire())
        try:
            while not pending.done():
                if handle:
                    await handle.checkpoint()
                await asyncio.wait({pending}, timeout=0.2)
            await pending
            if handle:
                await handle.checkpoint()
            yield
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            if not pending.cancelled() and pending.exception() is None:
                self._hardware_slots.release()

    def choices(self, requested: str, software: str) -> list[str]:
        families = list(FAMILIES) if requested == "auto" else [requested]
        index = 1 if software == "libx264" else 2
        return [
            f
            for f in families
            if f in FAMILIES
            and supported(f)
            and FAMILIES[f][index] in self.compiled
            and not (requested == "auto" and FAMILIES[f][index] in self.unavailable)
        ]

    async def verify(self) -> None:
        """Encode a few synthetic frames with each compiled hardware encoder.

        ``-encoders`` only lists what FFmpeg was built with; without this check, auto
        mode would fail over through absent devices on every job.
        """
        self.probing = True
        try:
            for family, spec in FAMILIES.items():
                if not supported(family):
                    continue
                for software, encoder in zip(("libx264", "libx265"), spec[1:], strict=True):
                    if encoder not in self.compiled:
                        continue
                    args = ["-f", "lavfi", "-i", "color=s=256x256:d=0.2", "-c:v", software]
                    args = hardware_command([*args, "-f", "null", "-"], family, self.device)
                    try:
                        async with self.hardware_slot(None):
                            await asyncio.wait_for(
                                run_command(ffmpeg_args(self.ffmpeg, args, progress=False)),
                                PROBE_TIMEOUT,
                            )
                    except (FFmpegError, OSError, TimeoutError) as error:
                        message = str(error)[-1000:] or f"设备检测超过 {PROBE_TIMEOUT} 秒"
                        self.outcomes[encoder] = {"usable": False, "error": message}
                        self.unavailable.add(encoder)
                    else:
                        self.outcomes[encoder] = {"usable": True, "error": None}
                        self.unavailable.discard(encoder)
        finally:
            self.probing = False

    def status(self, selected: str) -> dict[str, Any]:
        return {
            "selected": selected,
            "vaapi_device": self.device,
            "probing": self.probing,
            "families": [
                {
                    "value": family,
                    "label": spec[0],
                    "supported": supported(family),
                    "encoders": [
                        {
                            "name": encoder,
                            "compiled": encoder in self.compiled,
                            **self.outcomes.get(encoder, {"usable": None, "error": None}),
                        }
                        for encoder in spec[1:]
                    ],
                }
                for family, spec in FAMILIES.items()
            ],
        }

    async def run(
        self,
        commands: list[list[str]],
        requested: str,
        *,
        duration: float = 0,
        handle: ProcessHandle | None = None,
        on_progress: ProgressCallback | None = None,
        cwd: Path | None = None,
    ) -> dict[str, Any]:
        software = next((enc for args in commands if (enc := software_encoder(args))), None)
        families = self.choices(requested, software) if software and requested != "software" else []
        fallback = None
        if software and requested != "software":
            if any("-pass" in args or "-x265-params" in args for args in commands):
                families = []
                fallback = "目标大小压缩使用软件两遍编码"
            elif not families:
                fallback = "所选硬件编码器未编译，使用软件编码"

        async def execute(planned: list[list[str]]) -> None:
            count = len(planned)
            durations = []
            for args in planned:
                # Piece effects carry their own -t. Normalisation and final encoding
                # otherwise use the plan duration; two passes each count once.
                value = duration
                for index, option in enumerate(args[:-1]):
                    if option == "-t":
                        with suppress(ValueError):
                            value = float(args[index + 1])
                durations.append(value if math.isfinite(value) and value > 0 else 1.0)
            total = sum(durations)
            completed = 0.0
            for i, args in enumerate(planned):
                if handle and handle.on_log:
                    handle.on_log(f"FFmpeg 执行步骤 {i + 1}/{count}", "info")
                callback = (
                    (
                        lambda value, start=completed, weight=durations[i]: on_progress(
                            (start + max(0.0, min(1.0, value)) * weight) / total
                        )
                    )
                    if on_progress
                    else None
                )
                await run_command(
                    ffmpeg_args(self.ffmpeg, args),
                    duration=durations[i],
                    on_progress=callback,
                    handle=handle,
                    cwd=cwd,
                )
                completed += durations[i]
                if on_progress:
                    on_progress(completed / total)

        attempts = [
            (family, decode)
            for family in families
            for decode in ((True, False) if family in DECODERS else (False,))
        ]
        for family, decode in attempts:
            chosen = FAMILIES[family][1 if software == "libx264" else 2]
            try:
                async with self.hardware_slot(handle):
                    await execute(
                        [
                            hardware_command(args, family, self.device, decode=decode)
                            for args in commands
                        ]
                    )
                self.outcomes[chosen] = {"usable": True, "error": None}
                self.unavailable.discard(chosen)
                return {
                    "requested": requested,
                    "encoder": chosen,
                    "decoder": DECODERS[family] if decode else None,
                    "fallback": None,
                }
            except FFmpegOperationalError:
                raise
            except FFmpegError as error:
                fallback = str(error)[-1000:]
                self.outcomes[chosen] = {"usable": False, "error": fallback}
                if handle and handle.on_log:
                    handle.on_log(
                        f"硬件处理失败（{chosen}），正在尝试其他解码或编码方式", "warning"
                    )
                # Retry original commands from the beginning, overwriting partial outputs;
                # a failed hardware decode retries the same encoder with software decoding.
                # Cancellation and process-launch errors must not start another encode.
        if fallback and handle and handle.on_log:
            handle.on_log(f"使用软件编码：{fallback}", "warning")
        await execute(commands)
        # Encoders without hardware counterparts (e.g. libsvtav1) are reported as run.
        named = next(
            (a[i + 1] for a in commands for i in range(len(a) - 1) if a[i] == "-c:v"), None
        )
        return {
            "requested": requested,
            "encoder": software or (named if named and named != "copy" else "copy/other"),
            "decoder": None,
            "fallback": fallback,
        }
