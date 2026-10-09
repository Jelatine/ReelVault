import json
import os
import sqlite3
import zipfile

import pytest

from reelvault.auth_keys import KEY_FILE, decrypt_secret, encrypt_secret, read_key
from reelvault.backup import BackupError, create_backup, restore_backup
from reelvault.config import Settings
from reelvault.errors import APIError
from reelvault.models import TwoFactor, utcnow
from reelvault.two_factor import consume, new_recovery_codes


def test_backup_transports_key_and_restores_recovery_codes(client, settings, tmp_path):
    secret = "ABCDEFGHIJKLMNOP234567ABCDEFGHIJKLMNOP"
    with client.app.state.sessionmaker() as db:
        record = TwoFactor(
            user_id=1,
            secret_ciphertext=encrypt_secret(settings.data_dir, secret, create=True),
            enabled_at=utcnow(),
            last_counter=123,
        )
        codes = new_recovery_codes(record)
        db.add(record)
        db.commit()
        ciphertext = record.secret_ciphertext
    archive = create_backup(settings, tmp_path / "enabled.zip")
    with zipfile.ZipFile(archive) as source:
        assert "auth-key" in source.namelist()
        assert source.read("auth-key") == read_key(settings.data_dir / KEY_FILE)
        broken = tmp_path / "missing.zip"
        with zipfile.ZipFile(broken, "w") as dest:
            for name in source.namelist():
                if name != "auth-key":
                    dest.writestr(name, source.read(name))
    rejected = Settings(data_dir=tmp_path / "rejected")
    with pytest.raises(BackupError):
        restore_backup(broken, rejected)
    assert not rejected.db_path.exists()
    target = Settings(data_dir=tmp_path / "restored")
    restore_backup(archive, target)
    assert decrypt_secret(target.data_dir, ciphertext) == secret
    # Windows protects private files with ACLs, not mode bits.
    assert os.name == "nt" or (target.data_dir / KEY_FILE).stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(target.db_path) as db:
        counter, hashes = db.execute(
            "SELECT last_counter, recovery_hashes FROM two_factor"
        ).fetchone()
        assert counter == 123 and json.loads(hashes) == record.recovery_hashes
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    # Recovery works even if the local key becomes unavailable, and is single-use.
    record.recovery_hashes = record.recovery_hashes.copy()
    consume(record, tmp_path / "no-key", codes[0])
    assert len(record.recovery_hashes) == 9
    with pytest.raises(APIError) as error:
        consume(record, tmp_path / "no-key", codes[0])
    assert error.value.code == "totp_invalid"
    (settings.data_dir / KEY_FILE).unlink()
    with pytest.raises(BackupError):
        create_backup(settings, tmp_path / "must-not-publish.zip")
    assert not (tmp_path / "must-not-publish.zip").exists()
