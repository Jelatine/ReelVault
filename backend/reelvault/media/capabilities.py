"""Optional FFmpeg components: detect them once and reject edits that need missing ones."""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import ops
from .ffmpeg import FFmpegError, run_command

# Feature -> filters and encoders it needs. Distribution builds often omit some of them.
FEATURES: dict[str, tuple[str, ...]] = {
    "hdr_tone_mapping": ("zscale", "tonemap"),
    "deinterlace": ("bwdif",),
    "loudness": ("loudnorm",),
    "audio_denoise": ("afftdn",),
    "stabilize": ("vidstabdetect", "vidstabtransform"),
    "lut": ("lut3d", "lut1d"),
    "denoise": ("hqdn3d",),
    "subtitle_burn": ("subtitles",),
    "text_watermark": ("drawtext",),
    "transitions": ("xfade", "acrossfade"),
    "crop_detect": ("cropdetect",),
    "silence_detect": ("silencedetect",),
    "gif": ("palettegen", "paletteuse"),
    "webp": ("libwebp_anim",),
    "h265": ("libx265",),
    "av1": ("libsvtav1",),
    "av1_two_pass": ("libsvtav1 (two-pass)",),
    "lossless_rotate": ("-display_rotation",),
    "webm": ("libvpx-vp9", "libopus"),
    "mp3": ("libmp3lame",),
    "vmaf": ("libvmaf",),
}


@dataclass
class Capabilities:
    filters: set[str] = field(default_factory=set)
    encoders: set[str] = field(default_factory=set)
    av1_two_pass: bool | None = None
    # Command-line options from `-h long`, with their leading dash.
    options: set[str] = field(default_factory=set)
    # HDR to SDR conversion actually usable: libplacebo, opencl, vaapi or zscale.
    tone_mapper: str = "zscale"

    def missing(self, feature: str) -> list[str]:
        if feature == "av1_two_pass":
            return list(FEATURES[feature]) if self.av1_two_pass is False else []
        available = self.filters | self.encoders | self.options
        return [name for name in FEATURES[feature] if name not in available]

    def status(self) -> dict[str, bool]:
        return {
            feature: not self.missing(feature)
            for feature in FEATURES
            if feature != "av1_two_pass" or self.av1_two_pass is not None
        }


async def detect_av1_two_pass(ffmpeg: str) -> bool:
    """Test actual stats exchange, including backported builds; -pass may be ignored."""
    with tempfile.TemporaryDirectory(prefix="reelvault-av1-probe-") as directory:
        log = Path(directory) / "pass"
        common = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=64x64:rate=8:duration=1",
            "-c:v",
            "libsvtav1",
            "-preset",
            "10",
            "-svtav1-params",
            "lp=1",
            "-b:v",
            "100k",
            "-passlogfile",
            str(log),
        ]
        try:
            await run_command([*common, "-pass", "1", "-an", "-f", "null", "-"], timeout=15)
            stats = Path(f"{log}-0.log")
            if not stats.is_file() or stats.stat().st_size == 0:
                return False
            await run_command([*common, "-pass", "2", "-an", "-f", "null", "-"], timeout=15)
        except (FFmpegError, OSError):
            return False
        return True


async def detect_capabilities(ffmpeg: str) -> Capabilities | None:
    """None when FFmpeg cannot be queried; callers then skip the checks."""
    try:
        filters = await run_command([ffmpeg, "-hide_banner", "-filters"], timeout=30)
        encoders = await run_command([ffmpeg, "-hide_banner", "-encoders"], timeout=30)
        options = await run_command([ffmpeg, "-hide_banner", "-h", "long"], timeout=30)
    except (FFmpegError, OSError):
        return None
    capabilities = Capabilities(
        set(re.findall(r"^\s*\S{2,3}\s+(\S+)\s+\S*->\S*", filters.stdout.decode(), re.M)),
        set(re.findall(r"^\s*[VAS]\S{5}\s+(\S+)", encoders.stdout.decode(errors="replace"), re.M)),
        options=set(re.findall(r"^(-[\w:]+)", options.stdout.decode(errors="replace"), re.M)),
    )
    capabilities.av1_two_pass = (
        await detect_av1_two_pass(ffmpeg) if "libsvtav1" in capabilities.encoders else False
    )
    return capabilities


def required_features(edit: ops.EditParams) -> list[str]:
    """Features an edit always needs, independent of its source."""
    match edit:
        case ops.AdjustParams():
            return [
                *(["stabilize"] if edit.stabilize else []),
                *(["lut"] if edit.lut_asset_id else []),
                *(["denoise"] if edit.denoise else []),
            ]
        case ops.AudioParams():
            return [
                *(["loudness"] if edit.normalize else []),
                *(["audio_denoise"] if edit.denoise else []),
            ]
        case ops.SubtitleParams():
            # Embedded bitmap subtitles are overlaid instead; that depends on the source.
            return ["subtitle_burn"] if edit.subtitle_asset_id else []
        case ops.WatermarkParams():
            return ["text_watermark"] if edit.mode == "text" else []
        case ops.MergeParams():
            return ["transitions"] if edit.transition != "none" else []
        case ops.AnimationParams():
            return [edit.format]
        case ops.CompressParams():
            return compression_features(edit)
        case ops.MixedParams():
            return compression_features(edit.compress)
        case ops.ConvertParams():
            return ["webm"] if edit.format == "webm" else []
        case ops.ExtractAudioParams():
            return ["mp3"] if edit.format == "mp3" else []
    return []


def compression_features(edit: ops.CompressParams) -> list[str]:
    return ([] if edit.codec == "h264" else [edit.codec]) + (
        ["av1_two_pass"] if edit.codec == "av1" and edit.target_size_mb is not None else []
    )


def missing_components(capabilities: Capabilities | None, edit: ops.EditParams) -> list[str]:
    if capabilities is None:
        return []
    missing = (name for f in required_features(edit) for name in capabilities.missing(f))
    return list(dict.fromkeys(missing))
