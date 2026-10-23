"""Normalise source pictures for re-encoding: deinterlace interlaced sources and make
SDR derivatives of tagged HDR10/HLG sources; stream copies keep the original."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg import FFmpegError, ffmpeg_args, run_command
from .probe import MediaInfo

SDR_TAGS = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
STRIP_HDR_SIDE_DATA = (
    "sidedata=mode=delete:type=MASTERING_DISPLAY_METADATA,"
    "sidedata=mode=delete:type=CONTENT_LIGHT_LEVEL"
)
# GPU tone mappers in order of preference; zscale+tonemap on the CPU is the fallback.
TONE_MAPPERS = ("libplacebo", "opencl", "vaapi")
TONE_FILTERS = {"libplacebo": "libplacebo", "opencl": "tonemap_opencl", "vaapi": "tonemap_vaapi"}


@dataclass
class ToneMapping:
    mapper: str = "zscale"
    vaapi_device: str = "/dev/dri/renderD128"


# Chosen once at startup by detect_tone_mapper; every HDR conversion uses it.
TONE = ToneMapping()


@dataclass
class Deinterlacing:
    # frame: one frame per frame; field: one frame per field (double rate, smoother).
    mode: str = "frame"


DEINTERLACE = Deinterlacing()


def processed_fps(info: MediaInfo) -> float:
    """Frame rate of re-encoded output once the source is normalised."""
    rate = info.output_fps
    return rate * 2 if info.interlaced and DEINTERLACE.mode == "field" else rate


def tone_device_args(mapper: str | None = None) -> list[str]:
    """Global options that create the filter device; they precede the inputs."""
    match mapper or TONE.mapper:
        case "opencl":
            return ["-init_hw_device", "opencl=rv_tone", "-filter_hw_device", "rv_tone"]
        case "vaapi":
            device = f"vaapi=rv_tone:{TONE.vaapi_device}"
            return ["-init_hw_device", device, "-filter_hw_device", "rv_tone"]
    # libplacebo creates its own Vulkan device.
    return []


def tone_map_filter(transfer: str, mapper: str | None = None) -> str:
    """HDR10/HLG (or, with libplacebo, Dolby Vision) to 8-bit BT.709 SDR."""
    match mapper or TONE.mapper:
        case "libplacebo":
            # libplacebo also applies Dolby Vision reshaping metadata.
            return (
                "libplacebo=tonemapping=mobius:colorspace=bt709:color_primaries=bt709:"
                "color_trc=bt709:range=limited:format=yuv420p," + STRIP_HDR_SIDE_DATA
            )
        case "opencl":
            return (
                "format=p010le,hwupload,tonemap_opencl=tonemap=mobius:desat=2:t=bt709:m=bt709:"
                "p=bt709:r=tv:format=nv12,hwdownload,format=nv12,format=yuv420p,"
                + STRIP_HDR_SIDE_DATA
            )
        case "vaapi":
            return (
                "format=p010le,hwupload,tonemap_vaapi=format=nv12:t=bt709:m=bt709:p=bt709,"
                "hwdownload,format=nv12,format=yuv420p," + STRIP_HDR_SIDE_DATA
            )
    # Explicit input transfer handles containers missing some colour tags. Unknown
    # primaries/matrix are left to zscale: fail instead of guessing a colour gamut.
    return (
        f"zscale=tin={transfer}:t=linear:npl=100,format=gbrpf32le,"
        "zscale=p=bt709,tonemap=tonemap=mobius:desat=2,"
        "zscale=t=bt709:m=bt709:r=limited,format=yuv420p," + STRIP_HDR_SIDE_DATA
    )


def needs_tone_mapping(info: MediaInfo) -> bool:
    # Dolby Vision profile 5 has no HDR10 base layer; only libplacebo reads its metadata.
    return info.is_hdr or (info.dolby_vision_only and TONE.mapper == "libplacebo")


def sdr_filter(info: MediaInfo) -> str:
    return tone_map_filter(info.color_transfer or "smpte2084") if needs_tone_mapping(info) else ""


async def detect_tone_mapper(ffmpeg: str, filters: set[str], vaapi_device: str) -> str:
    """Use the first GPU tone mapper that converts a synthetic PQ frame, else zscale."""
    TONE.vaapi_device = vaapi_device
    source = (
        "color=s=64x64:d=0.2,format=yuv420p10le,"
        "setparams=color_trc=smpte2084:color_primaries=bt2020:colorspace=bt2020nc"
    )
    for mapper in TONE_MAPPERS:
        if TONE_FILTERS[mapper] not in filters:
            continue
        args = [*tone_device_args(mapper), "-f", "lavfi", "-i", source]
        args += ["-vf", tone_map_filter("smpte2084", mapper), "-f", "null", "-"]
        try:
            await asyncio.wait_for(run_command(ffmpeg_args(ffmpeg, args, progress=False)), 20)
        except (FFmpegError, OSError, TimeoutError):
            continue
        TONE.mapper = mapper
        return mapper
    TONE.mapper = "zscale"
    return "zscale"


def deinterlace_filter(info: MediaInfo) -> str:
    # Only frames flagged as interlaced are processed, so mixed content stays intact.
    # Rate-dependent plans use processed_fps, which doubles for send_field.
    if not info.interlaced:
        return ""
    mode = "send_field" if DEINTERLACE.mode == "field" else "send_frame"
    return f"bwdif=mode={mode}:parity=auto:deint=interlaced"


def source_filter(info: MediaInfo) -> str:
    return ",".join(f for f in (deinterlace_filter(info), sdr_filter(info)) if f)


def reencodes_video(args: list[str]) -> bool:
    if "-vn" in args:
        return False
    return not any(
        args[i] in {"-c", "-c:v"} and args[i + 1] == "copy" for i in range(len(args) - 1)
    )


def sdr_commands(
    commands: list[list[str]], sources: list[tuple[Path, MediaInfo]]
) -> list[list[str]]:
    """Insert source normalisation before source video filters, including complex graphs.

    Match actual input paths so uploaded overlays/palettes and intermediate files are
    never deinterlaced or tone mapped a second time.
    """
    normalised = {
        str(path.resolve()): info
        for path, info in sources
        if needs_tone_mapping(info) or info.interlaced
    }
    if not normalised:
        return commands
    result = []
    for original in commands:
        args = list(original)
        if not reencodes_video(args):
            result.append(args)
            continue
        inputs = [args[i + 1] for i, value in enumerate(args[:-1]) if value == "-i"]
        tone_mapped = False
        for index, path in enumerate(inputs):
            info = normalised.get(str(Path(path).resolve()))
            if info is None:
                continue
            tone = source_filter(info)
            if "-filter_complex" in args:
                pos = args.index("-filter_complex") + 1
                graph = args[pos]
                pattern = re.compile(rf"\[{index}:(?:{info.video_index}|v(?::0)?)\]")
                matches = list(pattern.finditer(graph))
                if not matches:
                    continue
                labels = [f"[rv_sdr_{index}_{n}]" for n in range(len(matches))]
                for match, label in reversed(list(zip(matches, labels, strict=True))):
                    graph = graph[: match.start()] + label + graph[match.end() :]
                prefix = f"[{index}:{info.video_index}]{tone}"
                if len(labels) > 1:
                    prefix += f",split={len(labels)}"
                args[pos] = prefix + "".join(labels) + ";" + graph
            elif index == 0:
                # FFmpeg applies only one of several -vf/-filter:v options.
                flag = next((f for f in ("-vf", "-filter:v") if f in args), None)
                if flag:
                    pos = args.index(flag) + 1
                    args[pos] = tone + "," + args[pos]
                else:
                    args[-1:-1] = ["-vf", tone]
            else:
                continue
            tone_mapped = tone_mapped or needs_tone_mapping(info)
        if tone_mapped:
            # A GPU tone mapper needs its device before the first input.
            device = tone_device_args()
            if device and "-init_hw_device" not in args:
                args[0:0] = device
        if (
            tone_mapped
            and args[-1] != "-"
            and Path(args[-1]).suffix.lower() not in {".png", ".gif", ".webp", ".jpg"}
        ):
            args[-1:-1] = SDR_TAGS
        result.append(args)
    return result
