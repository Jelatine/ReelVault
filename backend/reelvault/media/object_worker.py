"""Signal-controllable S3 transfers; credentials enter only through a private file."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from ..config import S3Config
from ..object_store import ObjectRef, ObjectStore, ObjectStoreError


def write_state(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def execute(request_path: Path) -> None:
    value = json.loads(request_path.read_text())
    config = S3Config.model_validate(value["config"])
    state_path = request_path.parent / "state.json"
    last = 0.0

    def progress(fraction: float) -> None:
        nonlocal last
        now = time.monotonic()
        if now - last >= 0.25 or fraction == 1:
            print(f"out_time_us={int(fraction * 1_000_000)}", flush=True)
            last = now

    with ObjectStore(config) as store:
        if value["operation"] == "put":

            def started(upload_id: str) -> None:
                write_state(state_path, {"upload_id": upload_id})
                print("out_time_us=150000", flush=True)

            ref = store.put(
                Path(value["source"]),
                value["key"],
                on_started=started,
                on_progress=progress,
                transfer_id=value["transfer_id"],
            )
            write_state(state_path, {"ref": ref.model_dump(mode="json")})
        elif value["operation"] == "get":
            store.download(
                ObjectRef.model_validate(value["ref"]),
                request_path.parent / "verified-original",
                on_progress=progress,
            )
        else:
            raise ValueError("Invalid S3 transfer operation")
    print("progress=end", flush=True)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("Expected a private S3 request file")
        execute(Path(sys.argv[1]))
    except Exception as error:
        # Never print the SDK traceback or the credential-bearing request payload.
        print(
            str(error) if isinstance(error, ObjectStoreError) else "S3 transfer failed",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
