"""TOTP and recovery-code consumption inside the caller's write transaction."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from pathlib import Path

from .auth_keys import decrypt_secret
from .errors import APIError
from .models import TwoFactor
from .totp import match_counter


def recovery_digest(code: str) -> str:
    return hashlib.sha256(code.encode("ascii")).hexdigest()


def new_recovery_codes(record: TwoFactor) -> list[str]:
    codes = [secrets.token_hex(16) for _ in range(10)]
    record.recovery_hashes = [recovery_digest(code) for code in codes]
    return ["-".join(code[i : i + 8] for i in range(0, 32, 8)) for code in codes]


def consume(record: TwoFactor, data_dir: Path, code: str | None) -> None:
    """Caller holds BEGIN IMMEDIATE and must commit the updated record atomically."""
    if not code:
        raise APIError(403, "请输入验证器验证码或恢复码", code="totp_required")
    code = code.strip()
    normalized = code.replace("-", "").lower()
    if re.fullmatch(r"[0-9a-f]{32}", normalized):
        digest = recovery_digest(normalized)
        for existing in record.recovery_hashes:
            if hmac.compare_digest(existing, digest):
                record.recovery_hashes = [h for h in record.recovery_hashes if h != existing]
                return
    else:
        try:
            secret = decrypt_secret(data_dir, record.secret_ciphertext)
        except (OSError, ValueError) as error:
            raise APIError(
                503, "验证器密钥不可用，请使用恢复码", code="totp_key_unavailable"
            ) from error
        counter = match_counter(secret, code, last_counter=record.last_counter)
        if counter is not None:
            record.last_counter = counter
            return
    raise APIError(403, "验证码无效或已使用", code="totp_invalid")
