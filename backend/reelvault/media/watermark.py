from __future__ import annotations

import shutil
from pathlib import Path

from .ops import MP4_FLAGS, OpError, OpPlan, Source, WatermarkParams, audio_for_mp4, maps, x264

FONT = Path(__file__).resolve().parents[1] / "resources" / "fonts" / "NotoSansCJKsc-Regular.otf"


def placement(p: WatermarkParams, w: str, h: str, item_w: str, item_h: str) -> tuple[str, str]:
    margin = f"min({w},{h})*{p.margin_percent / 100:.6f}"
    left, top = f"min({margin},({w}-{item_w})/2)", f"min({margin},({h}-{item_h})/2)"
    right, bottom = f"{w}-{item_w}-({left})", f"{h}-{item_h}-({top})"
    if p.position == "custom":
        return f"({w}-{item_w})*{p.x / 100:.6f}", f"({h}-{item_h})*{p.y / 100:.6f}"
    return {
        "top-left": (left, top),
        "top-right": (right, top),
        "bottom-left": (left, bottom),
        "bottom-right": (right, bottom),
        "center": (f"({w}-{item_w})/2", f"({h}-{item_h})/2"),
    }[p.position]


def plan_watermark(
    p: WatermarkParams, src: Source, out: Path, tmp: Path, asset: Path | None = None
) -> OpPlan:
    path, info = src
    path, out, tmp = path.resolve(), out.resolve(), tmp.resolve()
    args = ["-i", str(path)]
    if p.mode == "image":
        if asset is None:
            raise OpError("请选择图片素材")
        # A single decoded PNG is repeated by overlay until the main input ends.
        shutil.copyfile(asset, tmp / "watermark.png")
        args += ["-i", "watermark.png"]
        width = max(1, round(info.width * p.width_percent / 100))
        # Preserve aspect ratio, including portrait logos, within the video canvas.
        graph = (
            f"[1:v]scale=w={width}:h={info.height}:force_original_aspect_ratio=decrease,"
            f"format=rgba,colorchannelmixer=aa={p.opacity:.6f}[logo];"
        )
        x, y = placement(p, "main_w", "main_h", "overlay_w", "overlay_h")
        graph += f"[0:{info.video_index}][logo]overlay=x='{x}':y='{y}':eof_action=repeat[v]"
        args += ["-filter_complex", graph, "-map", "[v]"]
        if info.audio_index is not None:
            args += ["-map", f"0:{info.audio_index}"]
    else:
        if not FONT.is_file():
            raise OpError("内置中文字体缺失，请重新安装程序")
        shutil.copyfile(FONT, tmp / "font.otf")
        (tmp / "watermark.txt").write_text(p.text, encoding="utf-8")
        x, y = placement(p, "w", "h", "text_w", "text_h")
        # Text is loaded from a file and expansions are disabled: punctuation and
        # user-entered %{...} remain literal, never filter or expression syntax.
        filt = (
            "drawtext=fontfile=font.otf:textfile=watermark.txt:expansion=none:"
            f"fontsize={p.font_size}:fontcolor={p.color}@{p.opacity:.6f}:"
            f"borderw={p.border_width}:bordercolor=black@{p.opacity:.6f}:"
            f"box={int(p.box)}:boxcolor=black@{p.opacity * 0.4:.6f}:boxborderw=4:"
            f"x='{x}':y='{y}':fix_bounds=1"
        )
        args += [*maps(info), "-vf", filt]
    args += [*x264(p.crf), *audio_for_mp4(info), *MP4_FLAGS, str(out)]
    return OpPlan([args], info.duration, cwd=tmp)
