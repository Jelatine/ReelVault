import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from reelvault.auth_keys import KEY_FILE, cipher, decrypt_secret, encrypt_secret, read_key


def test_private_encrypted_secret_round_trip_and_tampering(tmp_path):
    secret = "ABCDEFGHIJKLMNOP234567ABCDEFGHIJKLMNOP"
    ciphertext = encrypt_secret(tmp_path, secret, create=True)
    assert secret not in ciphertext
    assert decrypt_secret(tmp_path, ciphertext) == secret
    # Windows protects private files with ACLs, not mode bits.
    assert os.name == "nt" or (tmp_path / KEY_FILE).stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        decrypt_secret(tmp_path, ciphertext[:-5] + "ABCDE")
    other = tmp_path / "other"
    other.mkdir()
    cipher(other, create=True)
    with pytest.raises(ValueError):
        decrypt_secret(other, ciphertext)


def test_missing_key_never_silently_replaced(tmp_path):
    ciphertext = encrypt_secret(tmp_path, "ABCDEFGHIJKLMNOP234567", create=True)
    (tmp_path / KEY_FILE).unlink()
    with pytest.raises(FileNotFoundError):
        decrypt_secret(tmp_path, ciphertext)
    assert not (tmp_path / KEY_FILE).exists()


def test_symlink_nonregular_and_public_key_are_rejected(tmp_path):
    cipher(tmp_path, create=True)
    key = tmp_path / KEY_FILE
    if os.name != "nt":  # Windows protects the key with ACLs, not mode bits
        key.chmod(0o644)
        with pytest.raises(ValueError):
            read_key(key)
        key.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(key)
    with pytest.raises(OSError):
        read_key(link)
    # A directory: POSIX opens it and rejects the type, Windows refuses to open it.
    with pytest.raises(PermissionError if os.name == "nt" else ValueError):
        read_key(tmp_path)
    key.write_bytes(b"short")
    with pytest.raises(ValueError):
        cipher(tmp_path, create=True)
    assert key.read_bytes() == b"short"


def test_concurrent_enrollments_publish_one_complete_key(tmp_path):
    def enroll(_):
        return encrypt_secret(tmp_path, "ABCDEFGHIJKLMNOP234567", create=True)

    with ThreadPoolExecutor(max_workers=8) as pool:
        encrypted = list(pool.map(enroll, range(32)))
    assert all(decrypt_secret(tmp_path, value) == "ABCDEFGHIJKLMNOP234567" for value in encrypted)
    assert sorted(p.name for p in tmp_path.iterdir()) == [KEY_FILE]
