"""RFC 4226/6238 primitives; enrollment and atomic replay protection are separate.

Reference vectors: https://www.rfc-editor.org/rfc/rfc6238#appendix-B
"""

from __future__ import annotations

import base64
import hmac
import re
import secrets
import struct
import time
from typing import Literal

Digest = Literal["sha1", "sha256", "sha512"]
PERIOD = 30


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def hotp(secret: str, counter: int, *, digits: int = 6, digest: Digest = "sha1") -> str:
    if digits not in (6, 8) or digest not in ("sha1", "sha256", "sha512"):
        raise ValueError("Unsupported OTP format")
    if type(counter) is not int or not 0 <= counter < 2**64:
        raise ValueError("Invalid OTP counter")
    secret = secret.upper()
    if not re.fullmatch(r"[A-Z2-7]{16,128}", secret):
        raise ValueError("Invalid OTP secret")
    try:
        key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    except ValueError as error:
        raise ValueError("Invalid OTP secret") from error
    result = hmac.digest(key, struct.pack(">Q", counter), digest)
    offset = result[-1] & 15
    binary = int.from_bytes(result[offset : offset + 4], "big") & 0x7FFFFFFF
    return f"{binary % (10**digits):0{digits}d}"


def match_counter(
    secret: str, code: str, *, now: float | None = None, last_counter: int = -1
) -> int | None:
    """Return an unused counter within one time step; caller must persist atomically.

    Six ASCII digits only, as configured for the authenticator. Consuming a
    future step also rejects older steps rather than accepting the code twice.
    """
    if not re.fullmatch(r"[0-9]{6}", code):
        return None
    counter = int((time.time() if now is None else now) // PERIOD)
    for candidate in (counter, counter - 1, counter + 1):
        if candidate < 0 or candidate <= last_counter:
            continue
        if hmac.compare_digest(hotp(secret, candidate), code):
            return candidate
    return None
