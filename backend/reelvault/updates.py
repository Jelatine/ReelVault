"""Check GitHub Releases for new versions and upgrade release-package installs in place."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .config import PACKAGE_DIR, Settings

log = logging.getLogger("reelvault.updates")

# Exit code asking the service manager (systemd Restart=on-failure, launchd KeepAlive)
# to start the process again after an upgrade.
RESTART_EXIT_CODE = 75

_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


class UpdateError(RuntimeError):
    pass


def parse_version(text: str) -> tuple[int, int, int, tuple[tuple[int, int | str], ...] | None]:
    m = _SEMVER.match(text.strip())
    if not m:
        raise ValueError(f"invalid version: {text}")
    pre = None
    if m.group(4):
        # Numeric identifiers sort before alphanumeric ones (semver §11).
        pre = tuple(
            (0, int(p)) if p.isdigit() else (1, p)  # type: ignore[misc]
            for p in m.group(4).split(".")
        )
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), pre


def is_newer(candidate: str, current: str) -> bool:
    c, k = parse_version(candidate), parse_version(current)
    if c[:3] != k[:3]:
        return c[:3] > k[:3]
    # A release sorts after any of its prereleases.
    if c[3] is None or k[3] is None:
        return c[3] is None and k[3] is not None
    return c[3] > k[3]


# ------------------------------------------------------------------ install mode


def detect_install_mode(settings: Settings, app_dir: Path) -> str:
    if settings.install_mode != "auto":
        return settings.install_mode
    if os.environ.get("REELVAULT_IN_DOCKER") or Path("/.dockerenv").exists():
        return "docker"
    if (app_dir.parent / ".git").exists():
        return "source"
    if (app_dir / "uv.lock").exists() and (app_dir / "pyproject.toml").exists():
        return "package"
    return "none"


def instructions(mode: str, repo: str, version: str | None) -> str:
    tag = version or "latest"
    if mode == "docker":
        return (
            "# docker compose\n"
            "docker compose pull && docker compose up -d\n\n"
            "# 或 docker run\n"
            f"docker pull ghcr.io/{repo.lower()}:{tag}\n"
            "docker rm -f reelvault && docker run -d --name reelvault -p 8080:8080 \\\n"
            f"  -v $PWD/data:/data ghcr.io/{repo.lower()}:{tag}"
        )
    if mode == "source":
        return "git pull\nmake install && make build\n# 然后重启 ReelVault"
    if mode == "package":
        return (
            f"curl -LO https://github.com/{repo}/releases/download/v{version}/"
            f"reelvault-{version}.tar.gz\n"
            f"tar xzf reelvault-{version}.tar.gz && cd reelvault-{version}\n"
            "sudo ./deploy/install.sh"
        )
    return f"请从 https://github.com/{repo}/releases 下载新版本"


# ------------------------------------------------------------------ HTTP helpers


def _request(url: str, token: str | None, accept: str) -> urllib.request.Request:
    headers = {"Accept": accept, "User-Agent": f"ReelVault/{__version__}"}
    # Only send the token to the GitHub API, never to asset download hosts.
    if token and "/repos/" in url and not url.startswith("file:"):
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def fetch_json(url: str, token: str | None = None, timeout: float = 15) -> Any:
    req = _request(url, token, "application/vnd.github+json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def download(url: str, dest: Path, token: str | None = None, timeout: float = 120) -> None:
    req = _request(url, token, "application/octet-stream")
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, 1024 * 1024)


# ------------------------------------------------------------------ updater


@dataclass
class ReleaseInfo:
    version: str
    tag: str
    name: str
    url: str
    notes: str
    published_at: str | None
    prerelease: bool
    assets: dict[str, str] = field(default_factory=dict)  # name -> download url


@dataclass
class UpdaterState:
    checked_at: float | None = None
    latest: ReleaseInfo | None = None
    check_error: str | None = None
    # idle | downloading | verifying | installing | restarting | failed
    phase: str = "idle"
    message: str = ""
    error: str | None = None


class Updater:
    def __init__(
        self,
        settings: Settings,
        *,
        app_dir: Path | None = None,
        current_version: str = __version__,
        busy: Callable[[], int] | None = None,
        request_restart: Callable[[], None] | None = None,
    ) -> None:
        self.settings = settings
        self.app_dir = (app_dir or PACKAGE_DIR.parent).resolve()
        self.current_version = current_version
        self.busy = busy or (lambda: 0)
        self.request_restart = request_restart or (lambda: None)
        self.state = UpdaterState()
        self.mode = detect_install_mode(settings, self.app_dir)
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------ status

    @property
    def update_available(self) -> bool:
        latest = self.state.latest
        try:
            return latest is not None and is_newer(latest.version, self.current_version)
        except ValueError:
            return False

    def auto_upgrade_blocker(self) -> str | None:
        """Why a one-click upgrade isn't possible here, or None when it is."""
        if not self.settings.allow_self_update:
            return "已通过 REELVAULT_ALLOW_SELF_UPDATE=false 禁用在线升级"
        if self.mode == "docker":
            return "Docker 部署请拉取新镜像后重建容器"
        if self.mode == "source":
            return "源码运行请使用 git pull 后重新编译"
        if self.mode != "package":
            return "当前安装方式不支持在线升级"
        if shutil.which(self.settings.uv) is None:
            return f"找不到 uv 命令（{self.settings.uv}）"
        if not os.access(self.app_dir, os.W_OK):
            return f"程序目录 {self.app_dir} 不可写"
        return None

    def status(self) -> dict[str, Any]:
        latest = self.state.latest
        version = latest.version if latest else None
        return {
            "current_version": self.current_version,
            "latest_version": version,
            "update_available": self.update_available,
            "release": None
            if latest is None
            else {
                "tag": latest.tag,
                "name": latest.name,
                "url": latest.url,
                "notes": latest.notes,
                "published_at": latest.published_at,
                "prerelease": latest.prerelease,
            },
            "checked_at": datetime.fromtimestamp(self.state.checked_at, UTC).isoformat()
            if self.state.checked_at
            else None,
            "check_error": self.state.check_error,
            "check_enabled": self.settings.update_check,
            "repo": self.settings.update_repo,
            "install_mode": self.mode,
            "can_auto_upgrade": self.auto_upgrade_blocker() is None,
            "auto_upgrade_blocker": self.auto_upgrade_blocker(),
            "instructions": instructions(self.mode, self.settings.update_repo, version),
            "phase": self.state.phase,
            "message": self.state.message,
            "error": self.state.error,
        }

    # ------------------------------------------------------------ checking

    def _fetch_latest(self) -> ReleaseInfo | None:
        api = self.settings.update_api_url.rstrip("/")
        base = f"{api}/repos/{self.settings.update_repo}"
        token = self.settings.github_token
        if self.settings.update_include_prereleases:
            releases = fetch_json(f"{base}/releases?per_page=20", token)
        else:
            releases = [fetch_json(f"{base}/releases/latest", token)]
        best: ReleaseInfo | None = None
        for r in releases:
            if r.get("draft"):
                continue
            tag = r.get("tag_name") or ""
            try:
                parse_version(tag)
            except ValueError:
                continue
            info = ReleaseInfo(
                version=tag.removeprefix("v"),
                tag=tag,
                name=r.get("name") or tag,
                url=r.get("html_url") or "",
                notes=(r.get("body") or "")[:20000],
                published_at=r.get("published_at"),
                prerelease=bool(r.get("prerelease")),
                assets={a["name"]: a["browser_download_url"] for a in r.get("assets") or []},
            )
            if best is None or is_newer(info.version, best.version):
                best = info
        return best

    async def check(self) -> dict[str, Any]:
        try:
            latest = await asyncio.to_thread(self._fetch_latest)
            self.state.latest = latest
            self.state.check_error = None
        except Exception as e:  # network errors, rate limits, bad JSON
            log.warning("update check failed: %s", e)
            self.state.check_error = f"检查更新失败：{e}"
        self.state.checked_at = time.time()
        return self.status()

    async def run_periodic(self) -> None:
        await asyncio.sleep(30)
        while True:
            await self.check()
            if self.update_available and self.state.latest:
                log.info("new version available: %s", self.state.latest.version)
            await asyncio.sleep(max(1, self.settings.update_check_interval_hours) * 3600)

    def start(self) -> None:
        if self.settings.update_check:
            self._task = asyncio.create_task(self.run_periodic(), name="update-check")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    # ------------------------------------------------------------ upgrading

    def _set(self, phase: str, message: str = "", error: str | None = None) -> None:
        self.state.phase, self.state.message, self.state.error = phase, message, error

    async def start_upgrade(self) -> None:
        blocker = self.auto_upgrade_blocker()
        if blocker:
            raise UpdateError(blocker)
        if self._lock.locked() or self.state.phase not in ("idle", "failed"):
            raise UpdateError("升级正在进行中")
        if self.busy():
            raise UpdateError("有正在运行或排队的任务，请等待完成后再升级")
        await self.check()
        if not self.update_available or self.state.latest is None:
            raise UpdateError(self.state.check_error or "已经是最新版本")
        latest = self.state.latest
        self._set("downloading", f"正在下载 v{latest.version}")
        asyncio.create_task(self._upgrade(latest), name="upgrade")

    async def _upgrade(self, release: ReleaseInfo) -> None:
        async with self._lock:
            try:
                await asyncio.to_thread(self._upgrade_sync, release)
            except Exception as e:
                log.exception("upgrade failed")
                self._set("failed", "升级失败，已恢复到原版本", str(e))
                return
            self._set("restarting", f"已升级到 v{release.version}，正在重启")
            log.info("upgraded to %s, restarting", release.version)
            await asyncio.sleep(0.5)
            self.request_restart()

    def _upgrade_sync(self, release: ReleaseInfo) -> None:
        name = f"reelvault-{release.version}.tar.gz"
        url = release.assets.get(name)
        sha_url = release.assets.get(f"{name}.sha256")
        if not url or not sha_url:
            raise UpdateError(f"发布 {release.tag} 中缺少安装包 {name} 或校验文件")

        work = self.settings.tmp_dir / f"update-{release.version}"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        try:
            archive = work / name
            download(url, archive, self.settings.github_token)
            download(sha_url, work / "sha256", self.settings.github_token)

            self._set("verifying", "正在校验安装包")
            expected = (work / "sha256").read_text().split()[0].lower()
            actual = hashlib.sha256(archive.read_bytes()).hexdigest()
            if actual != expected:
                raise UpdateError("安装包 SHA256 校验失败")

            extract = work / "extract"
            with tarfile.open(archive) as tar:
                # Skip macOS AppleDouble files (._name): tar on macOS adds them and
                # Alembic would try to load ._0001_*.py as a migration.
                members = [m for m in tar.getmembers() if not Path(m.name).name.startswith("._")]
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(extract, members=members, filter="data")
                else:  # pragma: no cover - Python < 3.11.4
                    for m in members:
                        if m.name.startswith("/") or ".." in Path(m.name).parts:
                            raise UpdateError(f"安装包包含非法路径 {m.name}")
                    tar.extractall(extract, members=members)
            src = extract / f"reelvault-{release.version}" / "backend"
            for required in ("pyproject.toml", "uv.lock", "reelvault/static/index.html"):
                if not (src / required).exists():
                    raise UpdateError(f"安装包不完整：缺少 {required}")

            self._set("installing", "正在安装新版本")
            self._swap_and_sync(src)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _uv_sync(self) -> None:
        env = {
            **os.environ,
            "UV_PYTHON_INSTALL_DIR": str(self.app_dir / ".python"),
            "UV_CACHE_DIR": str(self.app_dir / ".uv-cache"),
        }
        result = subprocess.run(
            [self.settings.uv, "sync", "--project", str(self.app_dir), "--frozen", "--no-dev"],
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
        if result.returncode != 0:
            raise UpdateError(f"安装依赖失败：{(result.stderr or result.stdout).strip()[-1500:]}")

    def _swap_and_sync(self, src: Path) -> None:
        """Replace the package and lock files; restore the previous ones on failure."""
        app = self.app_dir
        backup = app / ".upgrade-backup"
        shutil.rmtree(backup, ignore_errors=True)
        backup.mkdir()
        files = ["pyproject.toml", "uv.lock", ".python-version"]
        try:
            shutil.copytree(src / "reelvault", app / "reelvault.new")
            (app / "reelvault").rename(backup / "reelvault")
            (app / "reelvault.new").rename(app / "reelvault")
            for f in files:
                if (app / f).exists():
                    shutil.copy2(app / f, backup / f)
                if (src / f).exists():
                    shutil.copy2(src / f, app / f)
            self._uv_sync()
        except Exception:
            log.warning("upgrade failed, rolling back")
            shutil.rmtree(app / "reelvault.new", ignore_errors=True)
            if (backup / "reelvault").exists():
                shutil.rmtree(app / "reelvault", ignore_errors=True)
                (backup / "reelvault").rename(app / "reelvault")
            for f in files:
                if (backup / f).exists():
                    shutil.copy2(backup / f, app / f)
            try:
                self._uv_sync()
            except Exception as e:  # pragma: no cover - best effort
                log.error("re-sync after rollback failed: %s", e)
            raise
        finally:
            shutil.rmtree(backup, ignore_errors=True)
