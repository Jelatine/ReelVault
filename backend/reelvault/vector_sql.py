"""Fast native cosine distance, with the same calculation on extension-less Python."""

from __future__ import annotations

import math
import sqlite3
import struct
from typing import Any

import sqlite_vec


def cosine(a: bytes, b: bytes) -> float:
    if len(a) != 2048 or len(b) != 2048:
        raise ValueError("Invalid vector size")
    left, right = struct.unpack("<512f", a), struct.unpack("<512f", b)
    norm = math.sqrt(sum(x * x for x in left) * sum(y * y for y in right))
    return 1 - sum(x * y for x, y in zip(left, right, strict=True)) / norm


def register_vectors(connection: Any) -> None:
    try:
        connection.enable_load_extension(True)
        try:
            sqlite_vec.load(connection)
        finally:
            connection.enable_load_extension(False)
    except (AttributeError, sqlite3.NotSupportedError, sqlite3.OperationalError):
        connection.create_function("vec_distance_cosine", 2, cosine, deterministic=True)
