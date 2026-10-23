"""Carry chapters through edits: map source times to output times and write ffmetadata."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import ops

Chapter = dict[str, Any]  # {"start": float, "end": float, "title": str}
# Pieces shorter than this after cutting are dropped rather than written as chapters.
MIN_CHAPTER = 0.05
CONTAINERS = {"mp4", "mov", "m4v", "mkv", "webm"}
# Edits that keep the source timeline unchanged.
SAME_TIMELINE = (
    ops.RotateParams,
    ops.CropParams,
    ops.CompressParams,
    ops.MuteParams,
    ops.ConvertParams,
    ops.EmbedCoverParams,
    ops.AudioParams,
    ops.AdjustParams,
    ops.WatermarkParams,
    ops.SubtitleParams,
)


def _cut(chapters: list[Chapter], spans: list[tuple[float, float]]) -> list[Chapter]:
    """Keep the parts of chapters inside the kept spans, shifted to the output timeline."""
    result: list[Chapter] = []
    offset = 0.0
    for start, end in spans:
        for chapter in chapters:
            lo, hi = max(start, chapter["start"]), min(end, chapter["end"])
            if hi - lo < MIN_CHAPTER:
                continue
            piece = {"start": offset + lo - start, "end": offset + hi - start}
            last = result[-1] if result else None
            # A chapter split only by a removed range continues as one chapter.
            if (
                last
                and last["title"] == chapter["title"]
                and abs(last["end"] - piece["start"]) < 1e-3
            ):
                last["end"] = piece["end"]
            else:
                result.append({**piece, "title": chapter["title"]})
        offset += end - start
    return result


def _scale(chapters: list[Chapter], factor: float) -> list[Chapter]:
    return [{**c, "start": c["start"] / factor, "end": c["end"] / factor} for c in chapters]


def map_chapters(
    edit: ops.EditParams,
    chapters: list[list[Chapter]],
    durations: list[float],
    titles: list[str],
) -> list[Chapter] | None:
    """Output chapters for ``edit``, or None for edits whose timeline is not mapped.

    ``chapters``, ``durations`` and ``titles`` follow the edit's source order.
    """
    source = [c for c in chapters[0] if c["end"] > c["start"]] if chapters else []
    duration = durations[0] if durations else 0.0
    match edit:
        case ops.TrimParams():
            spans = [(s.start, min(s.end, duration)) for s in edit.segments]
            return _cut(source, [(s, e) for s, e in spans if e > s])
        case ops.SpeedParams():
            return _scale(source, edit.factor)
        case ops.MixedParams():
            mapped = source
            if edit.trim:
                mapped = _cut(mapped, [(edit.trim.start, min(edit.trim.end, duration))])
            if edit.speed:
                mapped = _scale(mapped, edit.speed.factor)
            return mapped
        case ops.MergeParams():
            if edit.transition != "none":
                return None
            result: list[Chapter] = []
            offset = 0.0
            for own, length, title in zip(chapters, durations, titles, strict=True):
                # A source without chapters becomes one chapter named after it.
                pieces = _cut(own, [(0, length)]) or [{"start": 0, "end": length, "title": title}]
                result += [
                    {**c, "start": c["start"] + offset, "end": c["end"] + offset} for c in pieces
                ]
                offset += length
            return result
        case _ if isinstance(edit, SAME_TIMELINE):
            return _cut(source, [(0, duration)]) if duration > 0 else list(source)
    return None


def _escape(value: str) -> str:
    for char in ("\\", "=", ";", "#", "\n"):
        value = value.replace(char, "\\" + char)
    return value


def ffmetadata(chapters: list[Chapter]) -> str:
    lines = [";FFMETADATA1"]
    for chapter in chapters:
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={round(chapter['start'] * 1000)}",
            f"END={round(chapter['end'] * 1000)}",
            f"title={_escape(str(chapter['title']).replace(chr(13), ' '))}",
        ]
    return "\n".join(lines) + "\n"


def with_chapters(command: list[str], metadata: Path | None) -> list[str]:
    """Replace the chapters an FFmpeg command writes; None removes them."""
    if metadata is None:
        return [*command[:-1], "-map_chapters", "-1", command[-1]]
    inputs = [i for i, arg in enumerate(command[:-1]) if arg == "-i"]
    position = inputs[-1] + 2
    result = [*command[:position], "-f", "ffmetadata", "-i", str(metadata), *command[position:]]
    return [*result[:-1], "-map_chapters", str(len(inputs)), result[-1]]


def combine(
    duration: float,
    embedded: list[Chapter],
    scenes: list[tuple[float, str]],
    manual: list[tuple[float, str]],
) -> list[Chapter]:
    """Chapter list by start time: manual names win over file chapters over scenes."""
    starts: dict[float, str] = {}
    for start, title in [*scenes, *((c["start"], c["title"]) for c in embedded), *manual]:
        if 0 <= start < duration:
            starts[round(float(start), 6)] = title
    if starts and 0 not in starts:
        starts[0] = "开头"
    times = sorted(starts)
    return [
        {"start": t, "end": times[i + 1] if i + 1 < len(times) else duration, "title": starts[t]}
        for i, t in enumerate(times)
    ]
