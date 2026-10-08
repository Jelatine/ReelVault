from __future__ import annotations

import argparse
import os
from pathlib import Path


def main() -> None:
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    parser = argparse.ArgumentParser(description="Optional private CLIP service for ReelVault")
    parser.add_argument("--cache", type=Path, default=Path("./models"))
    parser.add_argument("--prepare", action="store_true", help="Explicitly download pinned models")
    parser.add_argument(
        "--prepare-faces",
        action="store_true",
        help="Explicitly download pinned YuNet/SFace weights",
    )
    parser.add_argument(
        "--faces", action="store_true", help="Enable optional offline face embeddings"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--threads", type=int, choices=range(1, 33), default=2)
    args = parser.parse_args()
    if args.prepare_faces:
        from .faces import prepare_faces

        prepare_faces(args.cache)
        if not args.prepare:
            return
    if args.prepare:
        from .model import prepare

        prepare(args.cache)
        return
    # Serving is strictly offline. Missing model files cause a startup failure.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from .server import create_app

    app = create_app(
        args.cache,
        os.environ.get("REELVAULT_VISION_TOKEN", ""),
        args.threads,
        enable_faces=args.faces,
    )
    import uvicorn

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        workers=1,
        limit_concurrency=8,
        timeout_keep_alive=5,
    )


if __name__ == "__main__":
    main()
