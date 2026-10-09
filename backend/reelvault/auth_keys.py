"""Private local Fernet key, included in portable metadata backups."""

from __future__ import annotations

import os
import stat
import tempfile
from contextlib import suppress
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .fsutil import NONBLOCK, open_nofollow, private_mode

KEY_FILE = ".auth-key"


def read_key(path: Path) -> bytes:
    """Read a private regular file without following symlinks."""
    fd = open_nofollow(path, os.O_RDONLY | NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not private_mode(info.st_mode):
            raise ValueError("Authenticator key must be a private regular file")
        key = os.read(fd, 45)
        if len(key) != 44:
            raise ValueError("Invalid authenticator key")
        Fernet(key)
        return key
    finally:
        os.close(fd)


def cipher(data_dir: Path, *, create: bool = False) -> Fernet:
    path = data_dir / KEY_FILE
    try:
        return Fernet(read_key(path))
    except FileNotFoundError:
        if not create:
            raise
    # Publish only a complete key. A concurrent enrollment uses the winning key.
    fd, name = tempfile.mkstemp(prefix=".auth-key-", dir=data_dir)
    staged = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(Fernet.generate_key())
            stream.flush()
            os.fsync(stream.fileno())
        with suppress(FileExistsError):
            os.link(staged, path)
        return Fernet(read_key(path))
    finally:
        staged.unlink(missing_ok=True)


def encrypt_secret(data_dir: Path, secret: str, *, create: bool = False) -> str:
    return cipher(data_dir, create=create).encrypt(secret.encode("ascii")).decode("ascii")


def decrypt_secret(data_dir: Path, ciphertext: str) -> str:
    try:
        return cipher(data_dir).decrypt(ciphertext.encode("ascii")).decode("ascii")
    except (InvalidToken, UnicodeError) as error:
        raise ValueError("Authenticator secret cannot be decrypted") from error
