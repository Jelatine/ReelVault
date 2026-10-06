from __future__ import annotations

from pathlib import Path

from .ops import AnimationParams, OpError, OpPlan, Source


def plan_animation(p: AnimationParams, src: Source, out: Path, tmp: Path) -> OpPlan:
    path, info = src
    end = min(p.end, info.duration)
    if p.start >= end:
        raise OpError("动图片段不在视频时长范围内")
    duration = end - p.start
    path, tmp = path.resolve(), tmp.resolve()
    out = out.resolve().with_suffix("." + p.format)
    inputs = ["-ss", str(p.start), "-t", str(duration), "-i", str(path)]
    # Reset the timeline before resampling and preserve display aspect ratio,
    # including non-square source pixels. GIF and WebP permit odd dimensions.
    filt = (
        f"setpts=PTS-STARTPTS,fps={p.fps},scale={p.width}:trunc(ow/dar+0.5):flags=lanczos,setsar=1"
    )
    if p.format == "gif":
        palette = tmp / "palette.png"
        first = [
            *inputs,
            "-map",
            f"0:{info.video_index}",
            "-an",
            "-vf",
            f"{filt},palettegen=max_colors={p.colors}:stats_mode=full",
            "-frames:v",
            "1",
            "-update",
            "1",
            str(palette),
        ]
        second = [
            *inputs,
            "-i",
            str(palette),
            "-filter_complex",
            f"[0:{info.video_index}]{filt}[frames];[frames][1:v]"
            f"paletteuse=dither={p.dither}:diff_mode=rectangle[v]",
            "-map",
            "[v]",
            "-an",
            "-loop",
            "0" if p.loop else "-1",
            str(out),
        ]
        return OpPlan([first, second], duration, ext="gif")
    args = [
        *inputs,
        "-map",
        f"0:{info.video_index}",
        "-an",
        "-vf",
        f"{filt},format=bgra",
        "-c:v",
        "libwebp_anim",
        "-lossless",
        str(int(p.lossless)),
        "-quality",
        str(p.quality),
        "-compression_level",
        "4",
        "-loop",
        "0" if p.loop else "1",
        str(out),
    ]
    return OpPlan([args], duration, ext="webp")
