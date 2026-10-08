"""Optional Whisper inference in a process controlled by the regular job runner."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
MAX_CUES = 50000


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Whisper transcription / model preparation")
    parser.add_argument(
        "--model", default="base", choices=("tiny", "base", "small", "medium", "large-v3")
    )
    parser.add_argument("--cache", required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--audio")
    parser.add_argument("--output")
    parser.add_argument("--language", default="auto")
    args = parser.parse_args()
    if not args.prepare and (not args.audio or not args.output):
        parser.error("--audio and --output are required")
    import onnxruntime
    from faster_whisper import WhisperModel

    onnxruntime.disable_telemetry_events()

    try:
        model = WhisperModel(
            args.model,
            device="cpu",
            compute_type="int8",
            cpu_threads=args.threads,
            download_root=args.cache,
            local_files_only=not (args.download or args.prepare),
        )
    except Exception as error:
        raise RuntimeError("本地语音模型未就绪，请先准备模型或允许下载") from error
    if args.prepare:
        print("Model prepared for offline transcription", flush=True)
        return
    segments, info = model.transcribe(
        args.audio,
        language=None if args.language == "auto" else args.language,
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    cues = []
    text_bytes = 0
    for segment in segments:
        start, end = float(segment.start), float(segment.end)
        value = " ".join(segment.text.split())
        if not math.isfinite(start) or not math.isfinite(end) or end <= start or start < 0:
            raise ValueError("Whisper returned an invalid timestamp")
        if value:
            text_bytes += len(value.encode("utf-8"))
            cues.append({"start": start, "end": end, "text": value})
        if len(cues) > MAX_CUES or text_bytes > MAX_BYTES // 2:
            raise ValueError("Transcription exceeds the subtitle size limit")
        print(f"out_time_us={int(end * 1_000_000)}", flush=True)
    payload = json.dumps({"language": info.language, "cues": cues}, ensure_ascii=False)
    if len(payload.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Transcription exceeds the subtitle size limit")
    Path(args.output).write_text(payload, encoding="utf-8")
    print(f"out_time_us={int(info.duration * 1_000_000)}\nprogress=end", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr, flush=True)
        raise SystemExit(1) from None
