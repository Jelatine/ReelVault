import json
import os
import time
import uuid
from pathlib import Path

import pytest

from reelvault.service_sync import (
    HelperPaths,
    ServiceSync,
    ServiceSyncError,
    process_once,
    validate_unit,
)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="systemd sync is Linux-only")

TEMPLATE = Path(__file__).resolve().parents[2] / "deploy" / "reelvault.service"


@pytest.fixture
def helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[HelperPaths, list[str]]:
    base = tmp_path / "sync"
    (base / "requests").mkdir(parents=True)
    (base / "requests").chmod(0o1770)
    (base / "private").mkdir(mode=0o700)
    unit = tmp_path / "units" / "reelvault.service"
    unit.parent.mkdir()
    unit.write_text(TEMPLATE.read_text().replace("RestartSec=5", "RestartSec=7"))
    paths = HelperPaths(
        base=base,
        unit=unit,
        root_uid=os.getuid(),
        root_gid=os.getgid(),
        service_uid=os.getuid(),
        service_gid=os.getgid(),
    )
    reloads: list[str] = []
    monkeypatch.setattr(HelperPaths, "reload", lambda self: reloads.append(self.unit.read_text()))
    return paths, reloads


def request(paths: HelperPaths, action: str, transaction: str, **extra) -> dict:
    body = {
        "request_id": uuid.uuid4().hex,
        "transaction": transaction,
        "action": action,
        "expires_at": time.time() + 30,
        **extra,
    }
    path = paths.requests / "request.json"
    path.write_text(json.dumps(body))
    path.chmod(0o600)
    process_once(paths)
    assert not path.exists()
    result = json.loads((paths.requests / "result.json").read_text())
    assert result["request_id"] == body["request_id"]
    return result


def test_apply_commit_retry_and_rollback_preserve_exact_old_unit(helper) -> None:
    paths, reloads = helper
    old = paths.unit.read_bytes()
    tx = uuid.uuid4().hex
    new = TEMPLATE.read_text()
    assert request(paths, "apply", tx, unit=new)["ok"]
    assert paths.unit.read_text() == new and len(reloads) == 1
    assert request(paths, "apply", tx, unit=new)["ok"]
    assert len(reloads) == 1
    assert request(paths, "commit", tx)["ok"]
    assert json.loads(paths.state.read_text())["phase"] == "committed"
    # A lost commit acknowledgement still permits the app's rollback.
    assert request(paths, "rollback", tx)["ok"]
    assert paths.unit.read_bytes() == old and len(reloads) == 2
    assert not paths.state.exists()


@pytest.mark.parametrize(
    "bad",
    [
        ("User=reelvault", "User=root"),
        ("ExecStart=/opt/reelvault/.venv/bin/reelvault", "ExecStart=+/bin/sh"),
        ("NoNewPrivileges=true", "NoNewPrivileges=false"),
        ("EnvironmentFile=/etc/reelvault/reelvault.env", "EnvironmentFile=/tmp/root.env"),
        ("ReadWritePaths=/var/lib/reelvault", "ReadWritePaths=/etc"),
        ("Type=simple", "Type=simple\nExecStartPre=/bin/sh -c id"),
        ("Type=simple", "Type=simple\nUser=root"),
        ("Type=simple", "Type=simple\nAmbientCapabilities=CAP_SYS_ADMIN"),
        ("RestartSec=5", "RestartSec=0"),
        ("WantedBy=multi-user.target", "WantedBy=multi-user.target\nAlias=sshd.service"),
        ("Wants=network-online.target", "Wants=sshd.service"),
        ("Description=ReelVault video server", "Description=%h\\\nUser=root"),
    ],
)
def test_unsafe_or_ambiguous_units_never_change_root_files(helper, bad) -> None:
    paths, reloads = helper
    old = paths.unit.read_bytes()
    content = TEMPLATE.read_text().replace(*bad)
    with pytest.raises(ServiceSyncError):
        validate_unit(content)
    result = request(paths, "apply", uuid.uuid4().hex, unit=content)
    assert not result["ok"]
    assert paths.unit.read_bytes() == old and not paths.state.exists() and not reloads


def test_failed_reload_restores_unit_and_retains_backup_if_rollback_reload_fails(
    helper,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, reloads = helper
    old = paths.unit.read_bytes()

    def fail_once(self: HelperPaths) -> None:
        reloads.append(self.unit.read_text())
        if len(reloads) == 1:
            raise ServiceSyncError("reload failed")

    monkeypatch.setattr(HelperPaths, "reload", fail_once)
    result = request(paths, "apply", uuid.uuid4().hex, unit=TEMPLATE.read_text())
    assert not result["ok"] and "reload failed" in result["error"]
    assert paths.unit.read_bytes() == old and len(reloads) == 2 and not paths.state.exists()
    monkeypatch.setattr(
        HelperPaths, "reload", lambda _: (_ for _ in ()).throw(ServiceSyncError("failed"))
    )
    tx = uuid.uuid4().hex
    result = request(paths, "apply", tx, unit=TEMPLATE.read_text())
    assert not result["ok"] and "回滚失败" in result["error"]
    assert paths.unit.read_bytes() == old and paths.state.exists()
    monkeypatch.setattr(HelperPaths, "reload", lambda self: reloads.append(self.unit.read_text()))
    assert request(paths, "rollback", tx)["ok"]


def test_expired_requests_conflicting_transactions_and_root_edits(helper) -> None:
    paths, reloads = helper
    old = paths.unit.read_bytes()
    tx = uuid.uuid4().hex
    assert not request(paths, "apply", tx, unit=TEMPLATE.read_text(), expires_at=0)["ok"]
    assert paths.unit.read_bytes() == old and not reloads
    assert request(paths, "apply", tx, unit=TEMPLATE.read_text())["ok"]
    other = uuid.uuid4().hex
    assert not request(paths, "apply", other, unit=TEMPLATE.read_text())["ok"]
    assert not request(paths, "rollback", other)["ok"]
    paths.unit.write_text("# root administrator edit\n")
    assert not request(paths, "rollback", tx)["ok"]
    assert paths.unit.read_text() == "# root administrator edit\n" and paths.state.exists()


def test_symlink_and_wrong_directory_permissions_rejected_without_following(
    helper, tmp_path: Path
) -> None:
    paths, reloads = helper
    victim = tmp_path / "victim"
    victim.write_text("keep")
    pending = paths.requests / "request.json"
    pending.symlink_to(victim)
    process_once(paths)
    assert victim.read_text() == "keep" and not pending.is_symlink() and not reloads
    paths.requests.chmod(0o0770)
    with pytest.raises(ServiceSyncError, match="root 管理"):
        process_once(paths)
    paths.requests.chmod(0o1770)
    paths.unit.unlink()
    paths.unit.symlink_to(victim)
    result = request(paths, "apply", uuid.uuid4().hex, unit=TEMPLATE.read_text())
    assert not result["ok"] and victim.read_text() == "keep" and not paths.state.exists()


def test_stale_transaction_recovery_and_new_commit(helper) -> None:
    paths, reloads = helper
    old = paths.unit.read_bytes()
    assert request(paths, "apply", uuid.uuid4().hex, unit=TEMPLATE.read_text())["ok"]
    record = json.loads(paths.state.read_text())
    record["expires_at"] = 0
    paths.save_state(record)
    tx = uuid.uuid4().hex
    latest = TEMPLATE.read_text().replace("RestartSec=5", "RestartSec=9")
    assert request(paths, "apply", tx, unit=latest)["ok"]
    assert len(reloads) == 3 and paths.unit.read_text() == latest
    assert request(paths, "rollback", tx)["ok"] and paths.unit.read_bytes() == old


def test_client_timeout_leaves_expiring_request_for_safe_recovery(helper) -> None:
    paths, _ = helper
    client = ServiceSync(paths.requests, timeout=0.15)
    with pytest.raises(ServiceSyncError, match="超时"):
        client.send("apply", TEMPLATE.read_text())
    process_once(paths)
    result = json.loads((paths.requests / "result.json").read_text())
    assert not result["ok"] and "过期" in result["error"]
    assert not paths.state.exists()
