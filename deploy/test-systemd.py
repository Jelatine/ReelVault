"""Exercise the real Ubuntu installer/updater on disposable GitHub-hosted VMs.

Run as root in CI after make package VERSION=ci. This deliberately uses the real
/opt, /etc, service account, systemd path activation and constrained root helper.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import os
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)


def wait(check, *, timeout: float = 120):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = check()
            if last:
                return last
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    raise AssertionError(f"Timed out: {last}")


def main() -> None:
    if os.geteuid() != 0 or os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit(
            "Only run this destructive installation test as root on a disposable CI VM"
        )
    if not Path("/run/systemd/system").is_dir():
        raise SystemExit("A real systemd system manager is required")
    source = Path("dist/reelvault-ci").resolve()
    assert (source / "backend/reelvault/static/index.html").is_file()
    original_version = (source / "backend/reelvault/__init__.py").read_text().split('"')[1]
    password = secrets.token_hex(24)
    env = Path("/etc/reelvault/reelvault.env")
    env.parent.mkdir(exist_ok=True)
    dropin = Path("/etc/systemd/system/reelvault.service.d/ci.conf")
    dropin.parent.mkdir(exist_ok=True)
    dropin.write_text("[Service]\nEnvironment=REELVAULT_CI_DROPIN=keep\n")
    latest: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="reelvault-systemd-ci-") as temp:
        dist = Path(temp)

        class Handler(SimpleHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/repos/fixture/reelvault/releases/latest":
                    body = json.dumps(latest).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    super().do_GET()

            def log_message(self, *args) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(dist)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        release_url = f"http://127.0.0.1:{server.server_port}"
        env.write_text(
            "REELVAULT_DATA_DIR=/var/lib/reelvault\nREELVAULT_HOST=127.0.0.1\n"
            "REELVAULT_PORT=18088\nREELVAULT_UPDATE_CHECK=false\n"
            "REELVAULT_UPDATE_REPO=fixture/reelvault\n"
            f"REELVAULT_UPDATE_API_URL={release_url}\n"
            f"REELVAULT_ADMIN_USER=ci-systemd-admin\nREELVAULT_ADMIN_PASSWORD={password}\n"
        )
        env.chmod(0o640)
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

        def api(path: str, method: str = "GET", data: Any = None, raw: bool = False):
            body = (
                data
                if isinstance(data, bytes)
                else json.dumps(data).encode()
                if data is not None
                else None
            )
            request = urllib.request.Request(
                "http://127.0.0.1:18088" + path,
                data=body,
                method=method,
                headers={
                    "X-Requested-With": "ReelVault",
                    "Content-Type": "application/octet-stream"
                    if isinstance(data, bytes)
                    else "application/json",
                },
            )
            with opener.open(request, timeout=10) as response:
                return response.read() if raw else json.load(response)

        def release(version: str, *, bad_lock: bool = False, bad_sha: bool = False) -> None:
            root = dist / f"reelvault-{version}"
            shutil.copytree(source, root)
            for name in (
                "backend/reelvault/__init__.py",
                "backend/pyproject.toml",
                "backend/uv.lock",
            ):
                path = root / name
                path.write_text(path.read_text().replace(original_version, version))
            unit = root / "deploy/reelvault.service"
            unit.write_text(
                unit.read_text().replace(
                    "RestartSec=5", "RestartSec=7" if version == "99.0.0" else "RestartSec=8"
                )
            )
            if bad_lock:
                (root / "backend/uv.lock").write_text("broken lock [\n")
            name = f"reelvault-{version}.tar.gz"
            with tarfile.open(dist / name, "w:gz") as archive:
                archive.add(root, arcname=root.name)
            sha = "0" * 64 if bad_sha else hashlib.sha256((dist / name).read_bytes()).hexdigest()
            (dist / (name + ".sha256")).write_text(f"{sha}  {name}\n")
            latest.clear()
            latest.update(
                {
                    "tag_name": f"v{version}",
                    "name": version,
                    "prerelease": False,
                    "draft": False,
                    "body": "Systemd integration fixture",
                    "assets": [
                        {"name": n, "browser_download_url": f"{release_url}/{n}"}
                        for n in (name, name + ".sha256")
                    ],
                }
            )

        try:
            subprocess.run([str(source / "deploy/install.sh")], check=True)
            wait(lambda: api("/healthz")["version"] == original_version)
            assert "REELVAULT_SYSTEMD_SYNC=true" in env.read_text()
            subprocess.run([str(source / "deploy/install.sh")], check=True)
            wait(lambda: api("/healthz")["version"] == original_version)
            saved_env = env.read_bytes()
            saved_dropin = dropin.read_bytes()
            assert (
                str(run("systemctl", "show", "reelvault", "-p", "User", "--value")).strip()
                == "reelvault"
            )
            assert (
                run("systemctl", "show", "reelvault", "-p", "NoNewPrivileges", "--value").strip()
                == "yes"
            )
            assert run("systemctl", "is-active", "reelvault-service-sync.path").strip() == "active"
            for path in (
                "/usr/local/libexec/reelvault-service-sync.py",
                "/var/lib/reelvault-service-sync/requests",
            ):
                assert Path(path).stat().st_uid == 0
            assert (
                Path("/var/lib/reelvault-service-sync/requests").stat().st_mode & 0o7777 == 0o1770
            )
            subprocess.run(
                [
                    "systemd-analyze",
                    "verify",
                    "/etc/systemd/system/reelvault.service",
                    "/etc/systemd/system/reelvault-service-sync.service",
                    "/etc/systemd/system/reelvault-service-sync.path",
                ],
                check=True,
            )
            api("/api/auth/login", "POST", {"username": "ci-systemd-admin", "password": password})
            assert api("/api/system/update")["can_auto_upgrade"]
            clip = dist / "test.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=blue:size=320x240:rate=12:duration=1",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(clip),
                ],
                check=True,
            )
            data = clip.read_bytes()
            upload = api("/api/uploads", "POST", {"filename": "systemd.mp4", "size": len(data)})
            api(f"/api/uploads/{upload['id']}?offset=0", "PUT", data)
            video = api(f"/api/uploads/{upload['id']}/complete", "POST")
            video_id = video["id"]
            wait(lambda: api(f"/api/videos/{video_id}")["status"] == "ready")
            api(
                f"/api/videos/{video_id}",
                "PATCH",
                {"title": "Ubuntu systemd verification", "tags": ["CI"], "rating": 4},
            )
            before_pid = run("systemctl", "show", "reelvault", "-p", "MainPID", "--value").strip()
            release("99.0.0")
            assert api("/api/system/update/check", "POST")["latest_version"] == "99.0.0"
            api("/api/system/update/apply", "POST")
            wait(lambda: api("/healthz")["version"] == "99.0.0", timeout=240)
            assert (
                run("systemctl", "show", "reelvault", "-p", "MainPID", "--value").strip()
                != before_pid
            )
            assert (
                run("systemctl", "show", "reelvault", "-p", "RestartUSec", "--value").strip()
                == "7s"
            )
            assert env.read_bytes() == saved_env and dropin.read_bytes() == saved_dropin
            assert "REELVAULT_CI_DROPIN=keep" in run(
                "systemctl", "show", "reelvault", "-p", "Environment", "--value"
            )
            for version, flags in (("99.0.1", {"bad_lock": True}), ("99.0.2", {"bad_sha": True})):
                release(version, **flags)
                api("/api/system/update/check", "POST")
                api("/api/system/update/apply", "POST")
                wait(lambda: api("/api/system/update")["phase"] == "failed", timeout=240)
                assert api("/healthz")["version"] == "99.0.0"
                assert (
                    run("systemctl", "show", "reelvault", "-p", "RestartUSec", "--value").strip()
                    == "7s"
                )
                assert env.read_bytes() == saved_env and dropin.read_bytes() == saved_dropin
            # Inject a negative acknowledgement after the real root helper applies
            # and reloads the unit. Only this disposable VM's root-owned copy is
            # changed; production has no fault-injection environment or input flag.
            helper = Path("/usr/local/libexec/reelvault-service-sync.py")
            helper_source = helper.read_text()
            injection = '        result["ok"] = True\n'
            assert helper_source.count(injection) == 1
            helper.write_text(
                helper_source.replace(
                    injection,
                    '        result["ok"] = request["action"] != "apply"\n'
                    '        result["error"] = "Injected acknowledgement failure"\n',
                )
            )
            try:
                release("99.0.3")
                api("/api/system/update/check", "POST")
                api("/api/system/update/apply", "POST")
                wait(lambda: api("/api/system/update")["phase"] == "failed", timeout=240)
                assert "acknowledgement failure" in api("/api/system/update")["error"]
                assert api("/healthz")["version"] == "99.0.0"
                assert '"99.0.0"' in Path("/opt/reelvault/reelvault/__init__.py").read_text()
                assert (
                    run("systemctl", "show", "reelvault", "-p", "RestartUSec", "--value").strip()
                    == "7s"
                )
                assert not Path("/var/lib/reelvault-service-sync/private/transaction.json").exists()
            finally:
                helper.write_text(helper_source)
            result = api(f"/api/videos/{video_id}")
            assert result["title"] == "Ubuntu systemd verification" and result["rating"] == 4
            assert (
                hashlib.sha256(api(f"/api/videos/{video_id}/download", raw=True)).digest()
                == hashlib.sha256(data).digest()
            )
            assert list(Path("/var/lib/reelvault/backups").glob("before-upgrade-*.zip"))
            # Exercise root-side validation, not only the updater's preflight check.
            bad = (
                (source / "deploy/reelvault.service")
                .read_text()
                .replace("User=reelvault", "User=root")
            )
            code = (
                "from reelvault.service_sync import ServiceSync; "
                f"ServiceSync().send('apply', {bad!r})"
            )
            denied = subprocess.run(
                ["sudo", "-u", "reelvault", "/opt/reelvault/.venv/bin/python", "-c", code],
                cwd="/opt/reelvault",
                capture_output=True,
                text=True,
            )
            assert denied.returncode != 0 and "User=reelvault" in denied.stderr
            assert (
                run("systemctl", "show", "reelvault", "-p", "User", "--value").strip()
                == "reelvault"
            )
            assert not Path("/opt/reelvault/.upgrade-backup").exists()
            print(
                "PASS: real Ubuntu install/reinstall, constrained path helper, "
                "online upgrade/restart, "
                "rollback, SHA256, metadata/media, config and drop-in preservation"
            )
        finally:
            subprocess.run(
                [
                    "journalctl",
                    "-u",
                    "reelvault",
                    "-u",
                    "reelvault-service-sync",
                    "--no-pager",
                    "-n",
                    "160",
                ],
                check=False,
            )
            subprocess.run(
                ["systemctl", "stop", "reelvault", "reelvault-service-sync.path"], check=False
            )
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
