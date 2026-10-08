"""Small controllable worker: ffmpeg extraction and private model RPC; no ML dependencies."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from ..vision import MAX_IMAGE_BYTES, MODEL_ID, VisionClient


def index(manifest: Path, output: Path) -> None:
    params = json.loads(manifest.read_text())
    client = VisionClient(params["url"], params["token"])
    client.request("/health")
    frames = []
    for ordinal, timestamp in enumerate(params["times"]):
        target = output.parent / f"{ordinal:04d}.jpg"
        subprocess.run(
            [
                params["ffmpeg"],
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-ss",
                f"{timestamp:.6f}",
                "-i",
                params["source"],
                "-map",
                f"0:{params['video_index']}",
                "-an",
                "-sn",
                "-dn",
                "-frames:v",
                "1",
                "-vf",
                "scale=512:512:force_original_aspect_ratio=decrease",
                "-q:v",
                "3",
                str(target),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        if not target.is_file() or not 0 < target.stat().st_size <= MAX_IMAGE_BYTES:
            raise RuntimeError(f"无法提取第 {ordinal + 1} 个视频画面")
        values = client.image(target.read_bytes(), retries=True)
        frames.append({"time": timestamp, "vector": values})
        print(f"out_time_us={(ordinal + 1) * 1000000}\nprogress=continue", flush=True)
    output.write_text(json.dumps({"model": MODEL_ID, "frames": frames}))
    print("progress=end", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        index(args.manifest, args.output)
    except Exception as error:
        import sys

        from ..errors import APIError

        print(str(error.detail) if isinstance(error, APIError) else str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
