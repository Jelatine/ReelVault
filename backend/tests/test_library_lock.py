from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reelvault.backup import library_lock
from reelvault.config import Settings
from reelvault.main import create_app


def test_library_lock_excludes_other_process_and_releases(tmp_path: Path) -> None:
    script = """
import sys
from pathlib import Path
from reelvault.backup import BackupError, library_lock
try:
    with library_lock(Path(sys.argv[1])):
        pass
except BackupError:
    sys.exit(2)
"""

    def attempt() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )

    with pytest.raises(RuntimeError, match="release"), library_lock(tmp_path):
        result = attempt()
        assert result.returncode == 2, result.stderr
        raise RuntimeError("release")
    result = attempt()
    assert result.returncode == 0, result.stderr


def test_server_starts_with_library_lock(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/auth/me").status_code == 401
