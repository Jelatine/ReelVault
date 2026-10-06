from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .ops import MP4_FLAGS, CompositeParams, OpError, OpPlan, Source, aac, x264


def cells(p: CompositeParams) -> list[tuple[int, int, int, int]]:
    """Return even pixel rectangles (x, y, width, height) for each input."""
    if p.layout == "pip":
        w = max(2, math.floor(p.width * p.pip_scale / 200) * 2)
        h = max(2, math.floor(p.height * p.pip_scale / 200) * 2)
        x = math.floor((p.width - w) * p.pip_x / 100 + 0.5)
        y = math.floor((p.height - h) * p.pip_y / 100 + 0.5)
        return [(0, 0, p.width, p.height), (x, y, w, h)]
    n = len(p.video_ids)
    columns = n if p.layout == "horizontal" else 1 if p.layout == "vertical" else min(n, p.columns)
    rows = math.ceil(n / columns)
    w, h = p.width // (columns * 2) * 2, p.height // (rows * 2) * 2
    if min(w, h) < 2:
        raise OpError("拼接画布太小，无法容纳所有输入")
    return [(i % columns * w, i // columns * h, w, h) for i in range(n)]


def fit_filter(w: int, h: int, fit: str, background: str) -> str:
    # Scale using display aspect ratio before resetting non-square pixel SAR.
    if fit == "contain":
        return (
            f"scale=w='if(gt(dar,{w}/{h}),{w},max(2,trunc({h}*dar/2)*2))':"
            f"h='if(gt(dar,{w}/{h}),max(2,trunc({w}/dar/2)*2),{h})',setsar=1,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={background}"
        )
    return (
        f"scale=w='if(gt(dar,{w}/{h}),max({w},ceil({h}*dar/2)*2),{w})':"
        f"h='if(gt(dar,{w}/{h}),{h},max({h},ceil({w}/dar/2)*2))',setsar=1,"
        f"crop={w}:{h}:(iw-ow)/2:(ih-oh)/2"
    )


def plan_composite(
    p: CompositeParams,
    sources: list[Source],
    out: Path,
) -> tuple[OpPlan, dict[str, Any]]:
    if len(sources) != len(p.video_ids) or any(info.duration <= 0 for _, info in sources):
        raise OpError("拼接输入缺失或时长无效")
    durations = [info.duration for _, info in sources]
    duration = (
        max(durations)
        if p.duration_mode == "longest"
        else min(durations)
        if p.duration_mode == "shortest"
        else durations[0]
    )
    rectangles = cells(p)
    background = "0x" + p.background[1:]
    args: list[str] = []
    graph = []
    for index, ((path, info), (_, _, w, h)) in enumerate(zip(sources, rectangles, strict=True)):
        args += ["-i", str(path.resolve())]
        chain = (
            f"[{index}:{info.video_index}]setpts=PTS-STARTPTS,"
            f"tpad=start_mode=clone:start_duration={max(0, info.video_delay):.9f}:"
            f"stop_mode=clone:stop_duration={duration:.9f},"
            f"fps={p.fps:.9f},trim=duration={duration:.9f},setpts=PTS-STARTPTS,"
            f"{fit_filter(w, h, p.fit, background)},format=yuv420p,settb=AVTB"
        )
        if p.layout == "pip" and index == 1:
            chain += f",format=rgba,colorchannelmixer=aa={p.pip_opacity:.9f}"
        graph.append(chain + f"[v{index}]")
    if p.layout == "pip":
        x, y, _, _ = rectangles[1]
        graph.append(f"[v0][v1]overlay=x={x}:y={y}:eof_action=repeat:format=auto,format=yuv420p[v]")
    else:
        layout = "|".join(f"{x}_{y}" for x, y, _, _ in rectangles)
        inputs = "".join(f"[v{i}]" for i in range(len(sources)))
        graph.append(
            f"{inputs}xstack=inputs={len(sources)}:layout={layout}:fill={background},"
            f"pad={p.width}:{p.height}:0:0:color={background},format=yuv420p[v]"
        )
    selected = (
        [p.audio_source]
        if p.audio_mode == "source"
        else list(range(len(sources)))
        if p.audio_mode == "mix"
        else []
    )
    selected = [i for i in selected if sources[i][1].has_audio]
    for index in selected:
        info = sources[index][1]
        graph.append(
            f"[{index}:{info.audio_index}]aresample=48000,aformat=channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS,adelay={max(0, round(info.audio_delay * 48000))}S:all=1,"
            f"apad,atrim=duration={duration:.9f},asetpts=PTS-STARTPTS[a{index}]"
        )
    audio_label = f"a{selected[0]}" if selected else ""
    if len(selected) > 1:
        graph.append(
            "".join(f"[a{i}]" for i in selected)
            + f"amix=inputs={len(selected)}:duration=longest:dropout_transition=0:normalize=1[a]"
        )
        audio_label = "a"
    args += ["-filter_complex", ";".join(graph), "-map", "[v]"]
    if selected:
        args += ["-map", f"[{audio_label}]", *aac()]
    args += [*x264(p.crf), "-t", f"{duration:.9f}", *MP4_FLAGS, str(out.resolve())]
    actual = {
        "duration": duration,
        "width": p.width,
        "height": p.height,
        "fps": p.fps,
        "cells": [list(rect) for rect in rectangles],
        "audio_sources": selected,
    }
    return OpPlan([args], duration), actual
