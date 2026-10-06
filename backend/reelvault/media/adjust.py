from __future__ import annotations

import shutil
from pathlib import Path

from .ops import MP4_FLAGS, AdjustParams, OpError, OpPlan, Source, audio_for_mp4, maps, x264


def plan_adjust(
    p: AdjustParams,
    src: Source,
    out: Path,
    tmp: Path,
    lut: Path | None = None,
    dimension: int = 3,
) -> OpPlan:
    path, info = src
    path, out, tmp = path.resolve(), out.resolve(), tmp.resolve()
    commands = []
    filters = []
    if p.stabilize:
        commands.append(
            [
                "-i",
                str(path),
                "-map",
                f"0:{info.video_index}",
                "-an",
                "-vf",
                f"vidstabdetect=result=transforms.trf:shakiness={p.shakiness}:accuracy={p.accuracy}",
                "-f",
                "null",
                "-",
            ]
        )
        filters.append(
            f"vidstabtransform=input=transforms.trf:smoothing={p.smoothing}:"
            f"zoom={p.zoom}:optzoom={int(p.autozoom)}:crop=black:interpol=bicubic"
        )
    if p.denoise:
        filters.append(
            f"hqdn3d={p.denoise}:{p.denoise * 0.75}:{p.denoise * 1.5}:{p.denoise * 1.125}"
        )
    if p.brightness or p.contrast != 1 or p.saturation != 1:
        filters.append(
            f"eq=brightness={p.brightness}:contrast={p.contrast}:saturation={p.saturation}"
        )
    if p.lut_asset_id:
        if lut is None or dimension not in (1, 3):
            raise OpError("请选择有效的 LUT 素材")
        shutil.copyfile(lut, tmp / "grade.cube")
        filters.append(
            "lut3d=file=grade.cube:interp=tetrahedral"
            if dimension == 3
            else "lut1d=file=grade.cube:interp=cubic"
        )
    filters.append("pad=ceil(iw/2)*2:ceil(ih/2)*2")
    commands.append(
        [
            "-i",
            str(path),
            *maps(info),
            "-vf",
            ",".join(filters),
            *x264(p.crf),
            *audio_for_mp4(info),
            *MP4_FLAGS,
            str(out),
        ]
    )
    return OpPlan(commands, info.duration, cwd=tmp)
