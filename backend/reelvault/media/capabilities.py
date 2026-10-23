"""Optional FFmpeg components: detect them once and reject edits that need missing ones."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

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
    "webm": ("libvpx-vp9", "libopus"),
    "mp3": ("libmp3lame",),
}


@dataclass
class Capabilities:
    filters: set[str] = field(default_factory=set)
    encoders: set[str] = field(default_factory=set)

    def missing(self, feature: str) -> list[str]:
        available = self.filters | self.encoders
        return [name for name in FEATURES[feature] if name not in available]

    def status(self) -> dict[str, bool]:
        return {feature: not self.missing(feature) for feature in FEATURES}


async def detect_capabilities(ffmpeg: str) -> Capabilities | None:
    """None when FFmpeg cannot be queried; callers then skip the checks."""
    try:
        filters = await run_command([ffmpeg, "-hide_banner", "-filters"], timeout=30)
        encoders = await run_command([ffmpeg, "-hide_banner", "-encoders"], timeout=30)
    except (FFmpegError, OSError):
        return None
    return Capabilities(
        set(re.findall(r"^\s*\S{2,3}\s+(\S+)\s+\S*->\S*", filters.stdout.decode(), re.M)),
        set(re.findall(r"^\s*[VAS]\S{5}\s+(\S+)", encoders.stdout.decode(errors="replace"), re.M)),
    )


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
            return ["loudness"] if edit.normalize else []
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
            return ["h265"] if edit.codec == "h265" else []
        case ops.MixedParams():
            return ["h265"] if edit.compress.codec == "h265" else []
        case ops.ConvertParams():
            return ["webm"] if edit.format == "webm" else []
        case ops.ExtractAudioParams():
            return ["mp3"] if edit.format == "mp3" else []
    return []


def missing_components(capabilities: Capabilities | None, edit: ops.EditParams) -> list[str]:
    if capabilities is None:
        return []
    missing = (name for f in required_features(edit) for name in capabilities.missing(f))
    return list(dict.fromkeys(missing))
