"""Restricted systemd unit synchronization.

install.sh copies this stdlib-only module to a root-owned location. The systemd
path service runs that copy with /usr/bin/python3 -I, never imports app code, and
only accepts the fixed service account, executable, config and writable paths.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE = Path("/var/lib/reelvault-service-sync")
REQUESTS = BASE / "requests"
UNIT = Path("/etc/systemd/system/reelvault.service")
MAX_BYTES = 65536
MAX_UNIT_BYTES = 16384


class ServiceSyncError(RuntimeError):
    pass


def validate_unit(content: str) -> None:
    """Do not give the writable app permission to install arbitrary root units."""
    if len(content.encode()) > MAX_UNIT_BYTES or any(ord(c) < 32 and c != "\n" for c in content):
        raise ServiceSyncError("服务配置过长或包含控制字符")
    values: dict[tuple[str, str], str] = {}
    section = ""
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line in ("[Unit]", "[Service]", "[Install]"):
            section = line[1:-1]
            continue
        if "=" not in line or not section or "\\" in line or "%" in line:
            raise ServiceSyncError("服务配置包含不允许的语法")
        key, value = (s.strip() for s in line.split("=", 1))
        pair = (section, key)
        if pair in values:
            raise ServiceSyncError(f"服务配置重复指令：{key}")
        values[pair] = value
    fixed = {
        ("Service", "Type"): "simple",
        ("Service", "User"): "reelvault",
        ("Service", "Group"): "reelvault",
        ("Service", "WorkingDirectory"): "/opt/reelvault",
        ("Service", "EnvironmentFile"): "/etc/reelvault/reelvault.env",
        ("Service", "ExecStart"): "/opt/reelvault/.venv/bin/reelvault",
        ("Service", "Restart"): "on-failure",
        ("Service", "NoNewPrivileges"): "true",
        ("Service", "PrivateTmp"): "true",
        ("Service", "ProtectHome"): "read-only",
        ("Install", "WantedBy"): "multi-user.target",
    }
    for pair, value in fixed.items():
        if values.pop(pair, None) != value:
            raise ServiceSyncError(f"服务配置必须保留 {pair[1]}={value}")
    protect = values.pop(("Service", "ProtectSystem"), None)
    if protect not in ("full", "strict"):
        raise ServiceSyncError("服务配置必须保留文件系统保护")
    paths = values.pop(("Service", "ReadWritePaths"), "").split()
    if len(paths) != 2 or set(paths) != {"/var/lib/reelvault", str(REQUESTS)}:
        raise ServiceSyncError("服务配置包含不允许的可写路径")
    for name in ("After", "Wants"):
        deps = values.pop(("Unit", name), "").split()
        if "network-online.target" not in deps or set(deps) - {
            "network-online.target",
            "reelvault-service-sync.path",
        }:
            raise ServiceSyncError(f"服务配置包含不允许的依赖：{name}")
    description = values.pop(("Unit", "Description"), "")
    if not description or len(description) > 256:
        raise ServiceSyncError("服务描述缺失或过长")
    for pair, value in values.items():
        if pair in (("Service", "RestartSec"), ("Service", "TimeoutStopSec")):
            valid = bool(re.fullmatch(r"[1-9][0-9]{0,2}", value))
        elif pair == ("Service", "UMask"):
            valid = value in ("0027", "0077")
        elif pair[0] == "Service" and pair[1] in (
            "ProtectKernelTunables",
            "ProtectKernelModules",
            "ProtectControlGroups",
            "RestrictSUIDSGID",
            "LockPersonality",
        ):
            valid = value == "true"
        else:
            valid = False
        if not valid:
            raise ServiceSyncError(f"服务配置包含不允许的指令或值：{pair[1]}")


def secure_dir(path: Path, owner: int, *, writable_group: bool = False) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & 0o002:
        raise ServiceSyncError(f"辅助服务目录权限不正确：{path}")
    if info.st_mode & 0o020 and not (writable_group and info.st_mode & stat.S_ISVTX):
        raise ServiceSyncError(f"辅助服务目录必须由 root 管理：{path}")


def read_file(path: Path, owner: int) -> tuple[bytes, os.stat_result]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
            raise ServiceSyncError(f"辅助服务文件权限不正确：{path.name}")
        data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ServiceSyncError("辅助服务文件过大")
        return data, info


def atomic_write(path: Path, data: bytes, *, owner: int, group: int, mode: int) -> None:
    fd, name = tempfile.mkstemp(prefix=".sync-", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            os.fchown(stream.fileno(), owner, group)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


@dataclass
class HelperPaths:
    base: Path = BASE
    unit: Path = UNIT
    root_uid: int = 0
    root_gid: int = 0
    service_uid: int = 0
    service_gid: int = 0
    systemctl: str = "/usr/bin/systemctl"

    @property
    def requests(self) -> Path:
        return self.base / "requests"

    @property
    def state(self) -> Path:
        return self.base / "private" / "transaction.json"

    def reload(self) -> None:
        result = subprocess.run(
            [self.systemctl, "daemon-reload"],
            capture_output=True,
            text=True,
            timeout=15,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
        if result.returncode:
            raise ServiceSyncError(f"systemd 重载失败：{(result.stderr or result.stdout)[-1000:]}")

    def save_state(self, record: dict[str, Any]) -> None:
        atomic_write(
            self.state,
            json.dumps(record).encode(),
            owner=self.root_uid,
            group=self.root_gid,
            mode=0o600,
        )

    def restore(self, record: dict[str, Any]) -> None:
        current, _ = read_file(self.unit, self.root_uid)
        previous = base64.b64decode(record["previous"], validate=True)
        if current != previous and hashlib.sha256(current).hexdigest() != record["sha256"]:
            raise ServiceSyncError("服务配置已被其他操作修改，保留备份，请管理员检查")
        if current != previous:
            atomic_write(self.unit, previous, owner=self.root_uid, group=self.root_gid, mode=0o644)
        # A previous reload may have failed after the old bytes were restored.
        self.reload()
        self.state.unlink(missing_ok=True)


def handle_request(paths: HelperPaths, request: dict[str, Any]) -> None:
    if set(request) - {"request_id", "transaction", "action", "expires_at", "unit"}:
        raise ServiceSyncError("同步请求包含未知字段")
    for key in ("request_id", "transaction"):
        if not isinstance(request.get(key), str) or not re.fullmatch(r"[0-9a-f]{32}", request[key]):
            raise ServiceSyncError("同步请求标识无效")
    deadline = request.get("expires_at")
    now = time.time()
    if (
        not isinstance(deadline, (float, int))
        or not math.isfinite(deadline)
        or not now < deadline <= now + 120
    ):
        raise ServiceSyncError("同步请求已过期或有效期无效")
    action = request.get("action")
    record = json.loads(read_file(paths.state, paths.root_uid)[0]) if paths.state.exists() else None
    if action == "apply":
        content = request.get("unit")
        if not isinstance(content, str):
            raise ServiceSyncError("同步请求缺少服务配置")
        validate_unit(content)
        data = content.encode()
        digest = hashlib.sha256(data).hexdigest()
        if record:
            if record["transaction"] == request["transaction"]:
                if record["sha256"] != digest:
                    raise ServiceSyncError("同一事务的服务配置不一致")
                current, _ = read_file(paths.unit, paths.root_uid)
                if hashlib.sha256(current).hexdigest() != digest:
                    raise ServiceSyncError("同步事务尚未应用，请先回滚")
                return
            if record["phase"] != "committed":
                if time.time() <= record["expires_at"]:
                    raise ServiceSyncError("已有未完成的服务配置同步事务")
                paths.restore(record)
        previous, _ = read_file(paths.unit, paths.root_uid)
        if len(previous) > MAX_UNIT_BYTES:
            raise ServiceSyncError("现有服务配置过大，请管理员检查")
        record = {
            "transaction": request["transaction"],
            "sha256": digest,
            "previous": base64.b64encode(previous).decode(),
            "phase": "applied",
            "expires_at": time.time() + 300,
        }
        paths.save_state(record)
        if time.time() >= deadline:
            paths.restore(record)
            raise ServiceSyncError("同步请求已过期")
        try:
            if previous != data:
                atomic_write(
                    paths.unit, data, owner=paths.root_uid, group=paths.root_gid, mode=0o644
                )
                paths.reload()
        except Exception as error:
            try:
                paths.restore(record)
            except Exception as rollback:
                raise ServiceSyncError(
                    f"服务配置同步失败：{error}；回滚失败：{rollback}"
                ) from error
            raise
    elif action in ("commit", "rollback"):
        if "unit" in request:
            raise ServiceSyncError("确认或回滚请求不接受服务配置")
        if record is None and action == "rollback":
            return
        if record is None or record["transaction"] != request["transaction"]:
            raise ServiceSyncError("服务配置同步事务不存在或不匹配")
        if action == "rollback":
            paths.restore(record)
        else:
            current, _ = read_file(paths.unit, paths.root_uid)
            if hashlib.sha256(current).hexdigest() != record["sha256"]:
                raise ServiceSyncError("服务配置已变化，无法确认同步事务")
            record["phase"] = "committed"
            paths.save_state(record)
    else:
        raise ServiceSyncError("不支持的服务配置同步操作")


def process_once(paths: HelperPaths) -> None:
    """Root entry point; fixed directories, no caller-provided filesystem paths."""
    secure_dir(paths.base, paths.root_uid)
    secure_dir(paths.requests, paths.root_uid, writable_group=True)
    secure_dir(paths.state.parent, paths.root_uid)
    secure_dir(paths.unit.parent, paths.root_uid)
    path = paths.requests / "request.json"
    if not path.exists() and not path.is_symlink():
        return
    identity = path.lstat()
    result: dict[str, Any] = {"request_id": None, "ok": False}
    try:
        raw, identity = read_file(path, paths.service_uid)
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ServiceSyncError("同步请求必须是对象")
        if isinstance(request.get("request_id"), str) and re.fullmatch(
            r"[0-9a-f]{32}", request["request_id"]
        ):
            result["request_id"] = request["request_id"]
        handle_request(paths, request)
        result["ok"] = True
    except Exception as error:
        result["error"] = str(error)[-2000:]
    atomic_write(
        paths.requests / "result.json",
        json.dumps(result).encode(),
        owner=paths.root_uid,
        group=paths.service_gid,
        mode=0o640,
    )
    # A replacement request must remain for the path unit's next invocation.
    try:
        current = path.lstat()
        if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
            path.unlink()
    except FileNotFoundError:
        pass


class ServiceSync:
    def __init__(self, directory: Path = REQUESTS, *, timeout: float = 60) -> None:
        self.directory = directory
        self.timeout = timeout
        self.transaction = uuid.uuid4().hex

    @classmethod
    def available(cls) -> bool:
        try:
            secure_dir(BASE, 0)
            secure_dir(REQUESTS, 0, writable_group=True)
            protocol, _ = read_file(BASE / "protocol", 0)
            return protocol == b"1\n" and os.access(REQUESTS, os.W_OK | os.X_OK)
        except (OSError, ServiceSyncError):
            return False

    def send(self, action: str, content: str | None = None) -> None:
        request_id = uuid.uuid4().hex
        body: dict[str, Any] = {
            "request_id": request_id,
            "transaction": self.transaction,
            "action": action,
            "expires_at": time.time() + self.timeout - 20,
        }
        if content is not None:
            body["unit"] = content
        request_path = self.directory / "request.json"
        try:
            pending = json.loads(read_file(request_path, os.getuid())[0])
            if (
                pending.get("transaction") != self.transaction
                and pending.get("expires_at", 0) > time.time()
            ):
                raise ServiceSyncError("已有未完成的服务配置同步请求")
        except FileNotFoundError:
            pass
        atomic_write(
            request_path,
            json.dumps(body).encode(),
            owner=os.getuid(),
            group=os.getgid(),
            mode=0o600,
        )
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                result = json.loads((self.directory / "result.json").read_bytes())
                if result.get("request_id") == request_id:
                    if not result.get("ok"):
                        raise ServiceSyncError(result.get("error") or "服务配置同步失败")
                    return
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            time.sleep(0.1)
        raise ServiceSyncError(
            "服务配置同步超时，请检查 reelvault-service-sync.path 和辅助服务日志"
        )


if __name__ == "__main__":
    import pwd

    if os.geteuid() != 0:
        raise SystemExit("The service sync helper must run as root")
    account = pwd.getpwnam("reelvault")
    process_once(HelperPaths(service_uid=account.pw_uid, service_gid=account.pw_gid))
