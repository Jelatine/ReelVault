"""Hardware encoding of existing software-filter plans, with whole-plan fallback."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ffmpeg import FFmpegError, ProcessHandle, ProgressCallback, ffmpeg_args, run_command

FAMILIES = {
    "videotoolbox": ("VideoToolbox (macOS)", "h264_videotoolbox", "hevc_videotoolbox"),
    "qsv": ("Intel QSV", "h264_qsv", "hevc_qsv"),
    "vaapi": ("VAAPI (Linux)", "h264_vaapi", "hevc_vaapi"),
    "nvenc": ("NVIDIA NVENC", "h264_nvenc", "hevc_nvenc"),
}


async def detect_encoders(ffmpeg: str) -> set[str]:
    try:
        result = await run_command([ffmpeg, "-hide_banner", "-encoders"])
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
    if family == "videotoolbox":
        quality = max(1, min(100, round(100 - crf * 100 / 51)))
        rate = [] if bitrate else ["-q:v", str(quality)]
        options = ["-allow_sw", "0", *rate, "-pix_fmt", "yuv420p"]
    elif family == "qsv":
        rate = [] if bitrate else ["-global_quality", str(max(1, crf))]
        options = [*rate, "-preset", "medium", "-pix_fmt", "nv12"]
        if "-force_key_frames" in args:
            options += ["-forced_idr", "1"]
    elif family == "nvenc":
        rate = ["-rc", "vbr"] if bitrate else ["-rc", "constqp", "-qp", str(crf)]
        options = [*rate, "-preset", "p4", "-pix_fmt", "yuv420p"]
        if "-force_key_frames" in args:
            options += ["-forced-idr", "1"]
    else:
        options = ["-rc_mode", "VBR"] if bitrate else ["-rc_mode", "CQP", "-qp", str(crf)]
    result = []
    i = 0
    while i < len(args):
        if args[i] in {"-preset", "-crf", "-pix_fmt"}:
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
        upload = "format=nv12,hwupload"
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

    def choices(self, requested: str, software: str) -> list[str]:
        families = list(FAMILIES) if requested == "auto" else [requested]
        index = 1 if software == "libx264" else 2
        return [f for f in families if f in FAMILIES and FAMILIES[f][index] in self.compiled]

    def status(self, selected: str) -> dict[str, Any]:
        return {
            "selected": selected,
            "vaapi_device": self.device,
            "families": [
                {
                    "value": family,
                    "label": spec[0],
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
            for i, args in enumerate(planned):
                callback = (
                    (lambda value, stage=i: on_progress((stage + value) / count))
                    if on_progress
                    else None
                )
                await run_command(
                    ffmpeg_args(self.ffmpeg, args),
                    duration=duration,
                    on_progress=callback,
                    handle=handle,
                    cwd=cwd,
                )

        attempts = [
            (family, decode)
            for family in families
            for decode in ((True, False) if family in DECODERS else (False,))
        ]
        for family, decode in attempts:
            chosen = FAMILIES[family][1 if software == "libx264" else 2]
            try:
                await execute(
                    [
                        hardware_command(args, family, self.device, decode=decode)
                        for args in commands
                    ]
                )
                self.outcomes[chosen] = {"usable": True, "error": None}
                return {
                    "requested": requested,
                    "encoder": chosen,
                    "decoder": DECODERS[family] if decode else None,
                    "fallback": None,
                }
            except FFmpegError as error:
                fallback = str(error)[-1000:]
                self.outcomes[chosen] = {"usable": False, "error": fallback}
                # Retry original commands from the beginning, overwriting partial outputs;
                # a failed hardware decode retries the same encoder with software decoding.
                # Cancellation and process-launch errors must not start another encode.
        await execute(commands)
        return {
            "requested": requested,
            "encoder": software or "copy/other",
            "decoder": None,
            "fallback": fallback,
        }
