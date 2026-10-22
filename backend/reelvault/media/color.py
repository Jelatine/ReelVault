"""Make SDR derivatives of tagged HDR10/HLG sources; stream copies retain HDR."""

from __future__ import annotations

import re
from pathlib import Path

from .probe import MediaInfo

SDR_TAGS = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]


def sdr_filter(info: MediaInfo) -> str:
    if not info.is_hdr:
        return ""
    # Explicit input transfer handles containers missing some colour tags. Unknown
    # primaries/matrix are left to zscale: fail instead of guessing a colour gamut.
    return (
        f"zscale=tin={info.color_transfer}:t=linear:npl=100,format=gbrpf32le,"
        "zscale=p=bt709,tonemap=tonemap=mobius:desat=2,"
        "zscale=t=bt709:m=bt709:r=limited,format=yuv420p,"
        "sidedata=mode=delete:type=MASTERING_DISPLAY_METADATA,"
        "sidedata=mode=delete:type=CONTENT_LIGHT_LEVEL"
    )


def sdr_commands(
    commands: list[list[str]], sources: list[tuple[Path, MediaInfo]]
) -> list[list[str]]:
    """Insert tone mapping before source video filters, including complex graphs.

    Match actual input paths so uploaded overlays/palettes and intermediate SDR
    files are never tone mapped a second time.
    """
    hdr = {str(path.resolve()): info for path, info in sources if info.is_hdr}
    if not hdr:
        return commands
    result = []
    for original in commands:
        args = list(original)
        if "-vn" in args or any(
            args[i] in {"-c", "-c:v"} and args[i + 1] == "copy" for i in range(len(args) - 1)
        ):
            result.append(args)
            continue
        inputs = [args[i + 1] for i, value in enumerate(args[:-1]) if value == "-i"]
        changed = False
        for index, path in enumerate(inputs):
            info = hdr.get(str(Path(path).resolve()))
            if info is None:
                continue
            tone = sdr_filter(info)
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
                if "-vf" in args:
                    pos = args.index("-vf") + 1
                    args[pos] = tone + "," + args[pos]
                else:
                    args[-1:-1] = ["-vf", tone]
            else:
                continue
            changed = True
        if (
            changed
            and args[-1] != "-"
            and Path(args[-1]).suffix.lower() not in {".png", ".gif", ".webp", ".jpg"}
        ):
            args[-1:-1] = SDR_TAGS
        result.append(args)
    return result
