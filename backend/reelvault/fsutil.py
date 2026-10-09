"""Portable low-level opens for private files that must not be symlinks."""

from __future__ import annotations

import errno
import os
from pathlib import Path

# Windows has neither flag; O_BINARY keeps reads byte-exact there.
NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARY = getattr(os, "O_BINARY", 0)


def open_nofollow(path: Path, flags: int, mode: int = 0o777) -> int:
    """os.open that refuses a symlink at the final component."""
    if not _NOFOLLOW and os.path.islink(path):
        # Checked before opening; creating symlinks on Windows needs privileges.
        raise OSError(errno.ELOOP, "Refusing to follow a symbolic link", str(path))
    return os.open(path, flags | _NOFOLLOW | _BINARY, mode)


def private_mode(st_mode: int) -> bool:
    """No group/other permission bits. Windows uses ACLs and always reports 0o666."""
    return os.name == "nt" or not st_mode & 0o077
