"""Consistent metadata archives and offline restore (media is backed up separately)."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic.script import ScriptDirectory

from . import __version__
from .auth_keys import KEY_FILE, decrypt_secret, read_key
from .config import Settings
from .db import make_engine
from .locations import location_of, resolve_path
from .migrate import alembic_config, upgrade
from .models import new_id


class BackupError(RuntimeError):
    pass


def validate_auth_secrets(database: Path, data_dir: Path) -> None:
    """Reject an archive that would strand enabled authenticators after restore."""
    with closing(sqlite3.connect(database)) as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='two_factor'"
        ).fetchone():
            return
        for (encrypted,) in db.execute("SELECT secret_ciphertext FROM two_factor"):
            try:
                decrypt_secret(data_dir, encrypted)
            except (OSError, ValueError) as error:
                raise BackupError("备份中的验证器密钥缺失或损坏") from error


@contextmanager
def library_lock(data_dir: Path) -> Iterator[None]:
    """Exclude offline restores and a second server from an active library."""
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / ".library.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            # Windows locks a byte range starting at the current file position.
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
        else:
            import fcntl

        try:
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            raise BackupError("视频库正在使用，请停止 ReelVault 服务后恢复") from e
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def snapshot(source: Path, dest: Path) -> None:
    if not source.is_file():
        raise BackupError("数据库不存在")
    with (
        closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src,
        closing(sqlite3.connect(dest)) as target,
    ):
        src.backup(target, pages=256)
    dest.chmod(0o600)


def validate_database(path: Path) -> str:
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise BackupError("数据库完整性检查失败")
            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise BackupError("数据库包含无效关联")
            versions = db.execute("SELECT version_num FROM alembic_version").fetchall()
            if len(versions) != 1:
                raise BackupError("数据库迁移版本无效")
            revision = str(versions[0][0])
            script = ScriptDirectory.from_config(alembic_config("sqlite://"))
            if not script.get_revision(revision):
                raise BackupError("数据库版本不受支持")
            db.execute("SELECT id, username, password_hash FROM users").fetchall()
            db.execute("SELECT id, file_path, playable_path FROM videos").fetchall()
            return revision
    except BackupError:
        raise
    except Exception as e:
        raise BackupError("备份数据库无效或来自更新版本") from e


def create_backup(
    settings: Settings, output: Path | None = None, *, reason: str = "manual"
) -> Path:
    settings.data_dir = settings.data_dir.resolve()
    settings.ensure_dirs()
    if output is None:
        directory = settings.data_dir / "backups"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        output = directory / f"{reason}-{stamp}-{new_id()[:8]}.zip"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise BackupError("备份目标已存在，请使用新文件名")
    with tempfile.TemporaryDirectory(prefix="backup-", dir=settings.tmp_dir) as temporary:
        work = Path(temporary)
        database = work / "reelvault.db"
        snapshot(settings.db_path, database)
        revision = validate_database(database)
        validate_auth_secrets(database, settings.data_dir)
        key_path = settings.data_dir / KEY_FILE
        try:
            auth_key = read_key(key_path)
        except FileNotFoundError:
            auth_key = None
        config = json.dumps(settings.model_dump(mode="json"), ensure_ascii=False, indent=2).encode()
        with database.open("rb") as database_file:
            database_digest = hashlib.file_digest(database_file, "sha256").hexdigest()
        manifest: dict[str, Any] = {
            "format": "reelvault-metadata",
            "version": 1,
            "app_version": __version__,
            "created_at": datetime.now(UTC).isoformat(),
            "revision": revision,
            "media_included": False,
            "sha256": {
                "reelvault.db": database_digest,
                "config.json": hashlib.sha256(config).hexdigest(),
            },
        }
        archive = work / "archive.zip"
        if auth_key is not None:
            manifest["sha256"]["auth-key"] = hashlib.sha256(auth_key).hexdigest()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(database, "reelvault.db")
            z.writestr("config.json", config)
            if auth_key is not None:
                z.writestr("auth-key", auth_key)
            z.writestr("manifest.json", json.dumps(manifest))
        archive.chmod(0o600)
        # Copy to the destination filesystem before publishing the complete archive.
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as staged:
            staged_path = Path(staged.name)
        try:
            shutil.copyfile(archive, staged_path)
            staged_path.chmod(0o600)
            os.link(staged_path, output)
        finally:
            staged_path.unlink(missing_ok=True)
    return output


def backup_before_migration(settings: Settings) -> Path | None:
    if not settings.db_path.is_file() or settings.db_path.stat().st_size == 0:
        return None
    revision = validate_database(settings.db_path)
    head = ScriptDirectory.from_config(alembic_config("sqlite://")).get_current_head()
    return create_backup(settings, reason="before-migration") if revision != head else None


def unpack_backup(archive: Path, work: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            expected = {"reelvault.db", "config.json", "manifest.json"}
            if "auth-key" in names:
                expected.add("auth-key")
            if len(names) != len(expected) or set(names) != expected:
                raise BackupError("备份包内容无效")
            limits = [
                ("reelvault.db", 1024**3),
                ("config.json", 1024**2),
                ("manifest.json", 65536),
            ]
            if "auth-key" in expected:
                limits.append(("auth-key", 44))
            for name, limit in limits:
                if z.getinfo(name).file_size > limit:
                    raise BackupError("备份包超过恢复大小限制")
                with z.open(name) as src, (work / name).open("wb") as dest:
                    shutil.copyfileobj(src, dest)
                (work / name).chmod(0o600)
            manifest = json.loads((work / "manifest.json").read_text())
            if manifest.get("format") != "reelvault-metadata" or manifest.get("version") != 1:
                raise BackupError("备份格式不受支持")
            checked = ["reelvault.db", "config.json"]
            if "auth-key" in expected:
                checked.append("auth-key")
            for name in checked:
                with (work / name).open("rb") as f:
                    if hashlib.file_digest(f, "sha256").hexdigest() != manifest["sha256"][name]:
                        raise BackupError("备份校验失败")
            if "auth-key" in expected:
                os.replace(work / "auth-key", work / KEY_FILE)
                read_key(work / KEY_FILE)
            validate_auth_secrets(work / "reelvault.db", work)
            if validate_database(work / "reelvault.db") != manifest.get("revision"):
                raise BackupError("备份版本不一致")
            config = json.loads((work / "config.json").read_text())
            if not isinstance(config, dict) or set(config) - Settings.model_fields.keys():
                raise BackupError("备份配置无效")
            return dict(Settings(_env_file=None, **config).model_dump(mode="json"))
    except BackupError:
        raise
    except Exception as e:
        raise BackupError("无法读取备份包") from e


def restore_backup(archive: Path, settings: Settings, *, replace: bool = False) -> dict[str, Any]:
    settings.data_dir = settings.data_dir.resolve()
    settings.ensure_dirs()
    with library_lock(settings.data_dir):
        if settings.db_path.exists() and not replace:
            raise BackupError("目标库已有数据库，确认替换时使用 --replace")
        with tempfile.TemporaryDirectory(prefix="restore-", dir=settings.tmp_dir) as temporary:
            work = Path(temporary)
            config = unpack_backup(archive, work)
            staged = work / "reelvault.db"
            engine = make_engine(staged)
            try:
                upgrade(engine)
            finally:
                engine.dispose()
            with closing(sqlite3.connect(staged)) as db:
                db.execute("DELETE FROM two_factor WHERE enabled_at IS NULL")
                root = settings.data_dir
                missing = []
                row = db.execute(
                    "SELECT value FROM runtime_settings WHERE key='storage_locations'"
                ).fetchone()
                registry = (
                    json.loads(row[0])
                    if row
                    else {
                        "storage_locations": config.get("storage_locations", {}),
                        "storage_default": config.get("storage_default", "local"),
                    }
                )
                try:
                    configured = Settings(**registry)
                    roots = {**configured.storage_locations, **settings.storage_locations}
                    if (
                        configured.storage_default not in {"local", "s3"}
                        and configured.storage_default not in roots
                    ):
                        raise ValueError("存储默认位置无效")
                    media_settings = settings.model_copy(update={"storage_locations": roots})
                except Exception as error:
                    raise BackupError("备份存储位置配置无效") from error
                for rel, digest in db.execute("SELECT file_path, sha256 FROM media_assets"):
                    path = (root / rel).resolve()
                    if Path(rel).is_absolute() or not path.is_relative_to(root):
                        raise BackupError("数据库包含非法素材路径")
                    if not path.is_file():
                        missing.append(rel)
                    else:
                        with path.open("rb") as stream:
                            actual = hashlib.file_digest(stream, "sha256").hexdigest()
                        if actual != digest:
                            raise BackupError("素材文件校验失败，请恢复原始 assets/ 文件")
                for video_id, original, playable in db.execute(
                    "SELECT id, file_path, playable_path FROM videos"
                ):
                    for rel in (original, playable):
                        if not rel:
                            continue
                        if rel.startswith("s3/"):
                            # Archived originals live in object storage; only an
                            # unarchived one needs its local staging file restored.
                            try:
                                location_of(rel)
                            except ValueError as error:
                                raise BackupError("数据库包含非法媒体路径") from error
                            state = db.execute(
                                "SELECT state FROM original_objects WHERE path=?", (rel,)
                            ).fetchone()
                            if state is None:
                                raise BackupError("数据库缺少对象存储原视频记录")
                            if state[0] != "ready" and not (root / "objects" / rel[3:]).is_file():
                                missing.append(rel)
                            continue
                        try:
                            path = resolve_path(media_settings, rel)
                        except ValueError as error:
                            raise BackupError("数据库包含非法媒体路径") from error
                        except OSError:
                            missing.append(rel)
                            continue
                        if not path.is_file():
                            missing.append(rel)
                    assets = root / "derived" / video_id
                    for field, filename in (
                        ("has_poster", "poster.jpg"),
                        ("has_preview", "preview.mp4"),
                        ("has_sprite", "sprite.jpg"),
                    ):
                        if not (assets / filename).is_file():
                            db.execute(f"UPDATE videos SET {field}=0 WHERE id=?", (video_id,))
                if missing:
                    raise BackupError(
                        f"缺少 {len(missing)} 个媒体文件，请先恢复 library/、derived/ 和 assets/："
                        f"{missing[0]}"
                    )
                registry = {
                    "storage_locations": {
                        key: entry.model_dump(mode="json") for key, entry in roots.items()
                    },
                    "storage_default": configured.storage_default,
                }
                if row:
                    db.execute(
                        "UPDATE runtime_settings SET value=? WHERE key='storage_locations'",
                        (json.dumps(registry),),
                    )
                config.update(registry)
                # Device tokens and temporary uploads are not portable. Never resurrect them.
                db.execute("DELETE FROM sessions")
                db.execute("DELETE FROM share_grants")
                db.execute("DELETE FROM runtime_settings WHERE key='webdav'")
                db.execute("DELETE FROM uploads")
                db.execute(
                    "UPDATE jobs SET status='failed', error='恢复备份后需重新提交任务' "
                    "WHERE status IN ('running','queued','paused')"
                )
                db.execute(
                    "UPDATE videos SET status='error', error='恢复备份后需重新导入未完成的视频' "
                    "WHERE status='processing'"
                )
                db.commit()
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            validate_database(staged)
            config["data_dir"] = str(root)
            # Deployment paths belong to the destination, not the old machine.
            config["static_dir"] = None
            restored_config = root / "restored-config.json"
            config_staged = work / "restored-config.json"
            config_staged.write_text(json.dumps(config, ensure_ascii=False, indent=2))
            config_staged.chmod(0o600)
            safety = (
                create_backup(settings, reason="before-restore")
                if settings.db_path.exists()
                else None
            )
            rollback = work / "previous.db"
            if safety:
                snapshot(settings.db_path, rollback)
            previous_config = restored_config.read_bytes() if restored_config.exists() else None
            key_path = root / KEY_FILE
            key_staged = work / KEY_FILE
            try:
                previous_key = read_key(key_path)
            except FileNotFoundError:
                previous_key = None
            key_installed = False
            try:
                if settings.db_path.exists():
                    # Fold any existing WAL into its database before swapping.
                    with closing(sqlite3.connect(settings.db_path)) as db:
                        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                for suffix in ("-wal", "-shm"):
                    Path(str(settings.db_path) + suffix).unlink(missing_ok=True)
                staged.chmod(0o600)
                os.replace(staged, settings.db_path)
                os.replace(config_staged, restored_config)
                if key_staged.exists():
                    os.replace(key_staged, key_path)
                    key_installed = True
            except Exception:
                if safety:
                    os.replace(rollback, settings.db_path)
                else:
                    settings.db_path.unlink(missing_ok=True)
                if previous_config is not None:
                    restored_config.write_bytes(previous_config)
                    restored_config.chmod(0o600)
                else:
                    restored_config.unlink(missing_ok=True)
                if key_installed:
                    if previous_key is None:
                        key_path.unlink(missing_ok=True)
                    else:
                        key_path.write_bytes(previous_key)
                        key_path.chmod(0o600)
                raise
            return {
                "config_file": str(restored_config),
                "safety_backup": str(safety) if safety else None,
            }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--data-dir", type=Path)
    export.add_argument("--output", type=Path, required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("--data-dir", type=Path, required=True)
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    settings = Settings(**({"data_dir": args.data_dir} if args.data_dir else {}))
    settings.ensure_dirs()
    try:
        if args.command == "export":
            print(create_backup(settings, args.output))
        else:
            print(
                json.dumps(
                    restore_backup(args.archive, settings, replace=args.replace), ensure_ascii=False
                )
            )
    except (BackupError, OSError) as e:
        parser.exit(1, f"{e}\n")


if __name__ == "__main__":
    main()
