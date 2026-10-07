"""Isolated, signal-controllable output transfer. Invoked only by the job runner."""

from __future__ import annotations

import errno
import os
import shutil
import sys
import time
from pathlib import Path

CHUNK = 4 * 1024 * 1024


def transfer(source: Path, staged: Path) -> None:
    # Same filesystem: constant-time rename. Cross-device: bounded streaming copy.
    try:
        source.rename(staged)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        total = max(1, source.stat().st_size)
        written = 0
        last = 0.0
        with source.open("rb") as incoming, staged.open("xb") as outgoing:
            while chunk := incoming.read(CHUNK):
                outgoing.write(chunk)
                written += len(chunk)
                now = time.monotonic()
                if now - last >= 0.25:
                    print(f"out_time_us={int(written / total * 1_000_000)}", flush=True)
                    last = now
            outgoing.flush()
            os.fsync(outgoing.fileno())
        shutil.copystat(source, staged)
    print("out_time_us=1000000\nprogress=end", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Expected source and staging paths")
    transfer(Path(sys.argv[1]), Path(sys.argv[2]))
