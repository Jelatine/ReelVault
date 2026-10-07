from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import tarfile
import threading
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from reelvault import updates
from reelvault.config import Settings
from reelvault.db import make_engine
from reelvault.migrate import upgrade
from reelvault.service_sync import ServiceSync, ServiceSyncError, process_once
from reelvault.updates import UpdateError, Updater, is_newer, parse_version

from .test_service_sync import TEMPLATE
from .test_service_sync import helper as helper

REPO = "Jelatine/ReelVault"


def test_uv_sync_preserves_link_extra_but_rollback_accepts_older_manifest(tmp_path, monkeypatch):
    import subprocess

    settings = Settings(data_dir=tmp_path / "data", link_import_enabled=True)
    updater = Updater(settings, app_dir=tmp_path)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(updates.subprocess, "run", run)
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text('[project.optional-dependencies]\nlink-import=["yt-dlp"]\n')
    updater._uv_sync()
    assert calls[-1][-2:] == ["--extra", "link-import"]
    manifest.write_text('[project]\nname="older-reelvault"\n')
    updater._uv_sync()
    assert "--extra" not in calls[-1]


def test_version_ordering() -> None:
    assert is_newer("0.2.0", "0.1.0")
    assert is_newer("v1.0.0", "0.9.9")
    assert not is_newer("0.1.0", "0.1.0")
    assert is_newer("0.1.0", "0.1.0-rc.1")
    assert not is_newer("0.1.0-rc.1", "0.1.0")
    assert is_newer("0.1.0-rc.2", "0.1.0-rc.1")
    assert is_newer("0.1.0-rc.10", "0.1.0-rc.9")
    assert is_newer("0.1.0-rc.1", "0.1.0-beta.1")
    with pytest.raises(ValueError):
        parse_version("latest")


def release_json(version: str, base: str = "https://example.invalid") -> dict[str, Any]:
    name = f"reelvault-{version}.tar.gz"
    return {
        "tag_name": f"v{version}",
        "name": f"v{version}",
        "html_url": f"https://github.com/{REPO}/releases/tag/v{version}",
        "body": "## 更新内容\n- 新功能",
        "published_at": "2026-10-05T00:00:00Z",
        "prerelease": "-" in version,
        "draft": False,
        "assets": [
            {"name": name, "browser_download_url": f"{base}/{name}"},
            {"name": f"{name}.sha256", "browser_download_url": f"{base}/{name}.sha256"},
        ],
    }


def test_status_and_check_via_api(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates, "fetch_json", lambda url, token=None: release_json("9.9.9"))
    status = client.get("/api/system/update").json()
    assert status["current_version"] == updates.__version__
    assert status["latest_version"] is None and status["checked_at"] is None

    status = client.post("/api/system/update/check").json()
    assert status["latest_version"] == "9.9.9"
    assert status["update_available"] is True
    assert status["release"]["notes"].startswith("## 更新内容")
    # the test suite runs from a git checkout
    assert status["install_mode"] == "source"
    assert status["can_auto_upgrade"] is False
    assert "git pull" in status["instructions"]

    r = client.post("/api/system/update/apply")
    assert r.status_code == 409 and "git pull" in r.json()["detail"]


def test_check_error_is_reported(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str, token: str | None = None) -> Any:
        raise OSError("network down")

    monkeypatch.setattr(updates, "fetch_json", boom)
    status = client.post("/api/system/update/check").json()
    assert "network down" in status["check_error"]
    assert status["update_available"] is False


def test_prerelease_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    listing = [release_json("0.2.0-rc.1"), release_json("0.1.5"), {"tag_name": "nightly"}]
    monkeypatch.setattr(updates, "fetch_json", lambda url, token=None: listing)
    s = Settings(data_dir=tmp_path, update_include_prereleases=True, install_mode="none")
    up = Updater(s, app_dir=tmp_path, current_version="0.1.0")
    asyncio.run(up.check())
    assert up.state.latest and up.state.latest.version == "0.2.0-rc.1"


def test_docker_mode_shows_instructions(tmp_path: Path) -> None:
    s = Settings(data_dir=tmp_path, install_mode="docker")
    up = Updater(s, app_dir=tmp_path)
    status = up.status()
    assert status["can_auto_upgrade"] is False
    assert "docker compose pull" in status["instructions"]


# ---------------------------------------------------------------- package upgrades


def make_app_dir(root: Path, version: str) -> Path:
    app = root / "opt"
    (app / "reelvault" / "static").mkdir(parents=True)
    (app / "reelvault" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (app / "reelvault" / "static" / "index.html").write_text(version)
    (app / "pyproject.toml").write_text(f'version = "{version}"\n')
    (app / "uv.lock").write_text(f"lock {version}\n")
    return app


def make_release(
    root: Path, version: str, *, corrupt_sha: bool = False, unit: str | None = None
) -> Path:
    dist = root / "dist"
    pkg = dist / f"reelvault-{version}" / "backend"
    (pkg / "reelvault" / "static").mkdir(parents=True)
    (pkg / "reelvault" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (pkg / "reelvault" / "static" / "index.html").write_text(version)
    (pkg / "pyproject.toml").write_text(f'version = "{version}"\n')
    (pkg / "uv.lock").write_text(f"lock {version}\n")
    (pkg / ".python-version").write_text("3.13\n")
    if unit is not None:
        (pkg.parent / "deploy").mkdir()
        (pkg.parent / "deploy" / "reelvault.service").write_text(unit)
    # what tar on macOS adds; must not end up in the install
    (pkg / "reelvault" / "._junk.py").write_bytes(b"\x00\x05\x16\x07")
    name = f"reelvault-{version}.tar.gz"
    with tarfile.open(dist / name, "w:gz") as tar:
        tar.add(dist / f"reelvault-{version}", arcname=f"reelvault-{version}")
    digest = hashlib.sha256((dist / name).read_bytes()).hexdigest()
    if corrupt_sha:
        digest = "0" * 64
    (dist / f"{name}.sha256").write_text(f"{digest}  {name}\n")
    return dist


def run_upgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    uv: str = "true",
    corrupt_sha: bool = False,
    busy: int = 0,
    unit: str | None = None,
    systemd: bool = False,
) -> tuple[Updater, Path, list[bool]]:
    app = make_app_dir(tmp_path, "0.1.0")
    dist = make_release(tmp_path, "0.2.0", corrupt_sha=corrupt_sha, unit=unit)
    monkeypatch.setattr(
        updates, "fetch_json", lambda url, token=None: release_json("0.2.0", base="file://dist")
    )
    monkeypatch.setattr(
        updates,
        "download",
        lambda url, dest, token=None: shutil.copy(dist / url.rsplit("/", 1)[1], dest),
    )
    restarted: list[bool] = []
    settings = Settings(
        data_dir=tmp_path / "data",
        install_mode="package",
        uv=shutil.which(uv),
        systemd_sync=systemd,
    )
    monkeypatch.setattr(updates, "SYSTEMD_APP_DIR", app)
    settings.ensure_dirs()
    engine = make_engine(settings.db_path)
    upgrade(engine)
    engine.dispose()
    up = Updater(
        settings,
        app_dir=app,
        current_version="0.1.0",
        busy=lambda: busy,
        request_restart=lambda: restarted.append(True),
    )

    async def go() -> None:
        await up.start_upgrade()
        for _ in range(200):
            if up.state.phase == "failed" or restarted:
                return
            await asyncio.sleep(0.05)

    asyncio.run(go())
    return up, app, restarted


def test_package_upgrade_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema_head: str
) -> None:
    up, app, restarted = run_upgrade(tmp_path, monkeypatch)
    assert restarted == [True], up.state.error
    assert up.state.phase == "restarting"
    assert '"0.2.0"' in (app / "reelvault" / "__init__.py").read_text()
    assert (app / "reelvault" / "static" / "index.html").read_text() == "0.2.0"
    assert (app / "uv.lock").read_text() == "lock 0.2.0\n"
    assert not (app / "reelvault" / "._junk.py").exists()
    assert not (app / ".upgrade-backup").exists()
    assert not list((tmp_path / "data" / "tmp").glob("update-*"))
    archives = list((tmp_path / "data" / "backups").glob("before-upgrade-*.zip"))
    assert len(archives) == 1
    with zipfile.ZipFile(archives[0]) as archive:
        assert json.loads(archive.read("manifest.json"))["revision"] == schema_head


def test_backup_failure_aborts_upgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_backup(*args: Any, **kwargs: Any) -> None:
        raise OSError("backup disk full")

    monkeypatch.setattr(updates, "create_backup", fail_backup)
    up, app, restarted = run_upgrade(tmp_path, monkeypatch)
    assert not restarted
    assert up.state.phase == "failed" and "backup disk full" in (up.state.error or "")
    assert '"0.1.0"' in (app / "reelvault" / "__init__.py").read_text()


def test_failed_dependency_install_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    up, app, restarted = run_upgrade(tmp_path, monkeypatch, uv="false")
    assert not restarted
    assert up.state.phase == "failed" and "安装依赖失败" in (up.state.error or "")
    assert '"0.1.0"' in (app / "reelvault" / "__init__.py").read_text()
    assert (app / "uv.lock").read_text() == "lock 0.1.0\n"
    assert not (app / "reelvault.new").exists()
    assert not (app / ".python-version").exists()


def test_checksum_mismatch_aborts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    up, app, restarted = run_upgrade(tmp_path, monkeypatch, corrupt_sha=True)
    assert not restarted
    assert "SHA256" in (up.state.error or "")
    assert '"0.1.0"' in (app / "reelvault" / "__init__.py").read_text()


def test_upgrade_refused_while_jobs_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(UpdateError, match="等待完成"):
        run_upgrade(tmp_path, monkeypatch, busy=1)


@pytest.mark.parametrize("failure", [None, "apply", "commit", "rollback", "dependencies"])
def test_package_upgrade_real_helper_requests_and_atomic_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    helper,
    failure: str | None,
) -> None:
    paths, reloads = helper
    original_unit = paths.unit.read_bytes()
    stop = threading.Event()
    errors: list[Exception] = []

    def serve() -> None:
        while not stop.is_set():
            if (paths.requests / "request.json").exists():
                try:
                    process_once(paths)
                except Exception as error:
                    errors.append(error)
            stop.wait(0.01)

    class Client(ServiceSync):
        def __init__(self) -> None:
            super().__init__(paths.requests)

        @classmethod
        def available(cls) -> bool:
            return True

        def send(self, action: str, content: str | None = None) -> None:
            if failure == "rollback" and action == "rollback":
                raise ServiceSyncError("rollback unavailable")
            super().send(action, content)
            if action == failure or (failure == "rollback" and action == "apply"):
                raise ServiceSyncError(f"{action} acknowledgement lost")

    monkeypatch.setattr(updates, "ServiceSync", Client)
    worker = threading.Thread(target=serve)
    worker.start()
    try:
        up, app, restarted = run_upgrade(
            tmp_path,
            monkeypatch,
            systemd=True,
            unit=TEMPLATE.read_text(),
            uv="false" if failure == "dependencies" else "true",
        )
    finally:
        stop.set()
        worker.join(timeout=2)
    assert not worker.is_alive() and not errors
    if failure is None:
        assert restarted == [True] and up.state.phase == "restarting"
        assert paths.unit.read_text() == TEMPLATE.read_text() and len(reloads) == 1
        assert json.loads(paths.state.read_text())["phase"] == "committed"
        assert (app / "reelvault" / "static" / "index.html").read_text() == "0.2.0"
    else:
        assert not restarted and up.state.phase == "failed"
        assert (app / "reelvault" / "static" / "index.html").read_text() == "0.1.0"
        if failure == "rollback":
            assert "回滚需要检查" in up.state.message and "未确认" in (up.state.error or "")
            assert paths.state.exists() and paths.unit.read_text() == TEMPLATE.read_text()
        else:
            assert paths.unit.read_bytes() == original_unit and not paths.state.exists()
            assert len(reloads) == (0 if failure == "dependencies" else 2)


@pytest.mark.parametrize(
    "unit", [None, TEMPLATE.read_text().replace("User=reelvault", "User=root")]
)
def test_systemd_upgrade_rejects_missing_or_unsafe_unit_before_program_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unit: str | None,
) -> None:
    monkeypatch.setattr(ServiceSync, "available", classmethod(lambda cls: True))
    up, app, restarted = run_upgrade(tmp_path, monkeypatch, systemd=True, unit=unit)
    assert not restarted and up.state.phase == "failed"
    assert (app / "reelvault" / "static" / "index.html").read_text() == "0.1.0"
    assert not (app / ".upgrade-backup").exists()


def test_existing_systemd_install_requires_bootstrap_and_explicit_opt_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = make_app_dir(tmp_path, "0.1.0")
    monkeypatch.setattr(updates, "SYSTEMD_APP_DIR", app)
    monkeypatch.setattr(updates.sys, "platform", "linux")
    monkeypatch.setenv("INVOCATION_ID", "test-service")
    monkeypatch.setattr(ServiceSync, "available", classmethod(lambda cls: False))
    settings = Settings(data_dir=tmp_path, install_mode="package", uv=shutil.which("true"))
    updater = Updater(settings, app_dir=app)
    assert updater.systemd_sync_enabled
    assert "install.sh" in (updater.auto_upgrade_blocker() or "")
    settings.systemd_sync = False
    assert updater.auto_upgrade_blocker() is None
