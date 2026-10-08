"""Verify real encoding plans without importing the Unix-only application server."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import encoding, ops
from .ffmpeg import run_command
from .probe import probe

OPERATIONS = ("h264", "h265", "rotate", "merge")


async def verify_operation(
    family: str,
    operation: str,
    samples: dict[str, Path],
    workdir: Path,
    *,
    device: str = "/dev/dri/renderD128",
) -> dict[str, Any]:
    runtime = encoding.EncoderRuntime("ffmpeg", await encoding.detect_encoders("ffmpeg"), device)
    info = await probe("ffprobe", str(samples["a"]))
    out = workdir / f"{operation}.mp4"
    dimensions = (320, 240)
    if operation in {"h264", "h265"}:
        plan = ops.plan_compress(
            ops.CompressParams(codec=operation), (samples["a"], info), out, workdir
        )
    elif operation == "rotate":
        plan = ops.plan_rotate(ops.RotateParams(angle=90), (samples["a"], info), out)
        dimensions = (240, 320)
    elif operation == "merge":
        second = await probe("ffprobe", str(samples["b"]))
        plan = ops.plan_merge(
            ops.MergeParams(video_ids=["a", "b"], mode="reencode"),
            [(samples["a"], info), (samples["b"], second)],
            out,
            workdir,
        )
    else:
        raise ValueError(f"Unknown operation: {operation}")
    result = await runtime.run(plan.commands, family, duration=plan.duration, cwd=plan.cwd)
    expected = encoding.FAMILIES[family][2 if operation == "h265" else 1]
    # Explicit checks also work when Python is invoked with -O.
    if result["fallback"] is not None or result["encoder"] != expected:
        raise RuntimeError(f"Hardware encoding required: {result}")
    produced = await probe("ffprobe", str(out))
    if (
        produced.video_codec != ("hevc" if operation == "h265" else "h264")
        or (produced.width, produced.height) != dimensions
        or produced.audio_codec != "aac"
        or abs(produced.duration - plan.duration) >= 0.2
    ):
        raise RuntimeError(f"Unexpected output: {produced}")
    await run_command(
        [
            "ffmpeg", "-hide_banner", "-v", "error", "-xerror", "-i", str(out),
            "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-",
        ]
    )  # fmt: skip
    return {
        **result,
        "video_codec": produced.video_codec,
        "audio_codec": produced.audio_codec,
        "width": produced.width,
        "height": produced.height,
        "duration": produced.duration,
        "expected_duration": plan.duration,
        "decoded": True,
    }


async def check(family: str, device: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "family": family,
        "device": device if family == "vaapi" else None,
        "passed": False,
        "operations": [],
    }
    try:
        version = await run_command(["ffmpeg", "-version"])
        report["ffmpeg"] = version.stdout.decode(errors="replace").splitlines()[0]
        with tempfile.TemporaryDirectory(prefix="reelvault-hardware-") as directory:
            workdir = Path(directory)
            samples = {}
            for name, duration in (("a", 4), ("b", 3)):
                path = workdir / f"source-{name}.mp4"
                await run_command(
                    [
                        "ffmpeg", "-hide_banner", "-v", "error", "-y",
                        "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={duration}",
                        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "25",
                        "-c:a", "aac", "-shortest", str(path),
                    ]
                )  # fmt: skip
                samples[name] = path
            for operation in OPERATIONS:
                entry: dict[str, Any] = {"operation": operation, "passed": False}
                try:
                    entry.update(
                        await verify_operation(family, operation, samples, workdir, device=device)
                    )
                    entry["passed"] = True
                except Exception as error:
                    entry["error"] = str(error)
                report["operations"].append(entry)
        report["passed"] = all(entry["passed"] for entry in report["operations"])
    except Exception as error:
        report["error"] = str(error)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("family", choices=encoding.FAMILIES)
    parser.add_argument("--device", default="/dev/dri/renderD128")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(check(args.family, args.device))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
