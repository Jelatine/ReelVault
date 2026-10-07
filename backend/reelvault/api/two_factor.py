from __future__ import annotations

import io
from datetime import timedelta
from typing import Any
from urllib.parse import quote, urlencode

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.orm import Session

from .. import auth as A
from ..auth_keys import decrypt_secret, encrypt_secret
from ..config import Settings
from ..db import get_db
from ..errors import APIError
from ..models import AuthSession, TwoFactor, User, utcnow
from ..storage import lock_budget
from ..totp import match_counter, new_secret
from ..two_factor import consume, new_recovery_codes
from .deps import get_settings

router = APIRouter(prefix="/api/auth/totp", tags=["auth"])


class VerifyBody(BaseModel):
    current_password: str = Field(max_length=256)
    code: str | None = Field(None, max_length=64)


def guarded_user(db: Session, request: Request, auth: A.CurrentAuth, body: VerifyBody) -> User:
    limiter: A.LoginLimiter = request.app.state.totp_limiter
    ip = request.client.host if request.client else "unknown"
    lock_budget(db)
    limiter.check(ip)
    user = db.get(User, auth.user_id, populate_existing=True)
    if user is None or not A.verify_password(user.password_hash, body.current_password):
        limiter.fail(ip)
        raise APIError(400, "当前密码不正确", code="current_password_incorrect")
    return user


def verify_factor(
    db: Session, request: Request, record: TwoFactor, settings: Settings, code: str | None
) -> None:
    try:
        consume(record, settings.data_dir, code)
    except APIError as error:
        if error.code == "totp_invalid":
            request.app.state.totp_limiter.fail(
                request.client.host if request.client else "unknown"
            )
        raise


def revoke_others(db: Session, auth: A.CurrentAuth) -> None:
    db.execute(
        delete(AuthSession).where(
            AuthSession.user_id == auth.user_id, AuthSession.id != auth.session_id
        )
    )


@router.get("")
def factor_status(
    response: Response,
    auth: A.CurrentAuth = Depends(A.require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    record = db.get(TwoFactor, auth.user_id)
    return {
        "enabled": bool(record and record.enabled_at),
        "recovery_codes_remaining": len(record.recovery_hashes)
        if record and record.enabled_at
        else 0,
    }


@router.post("/enroll")
def enroll(
    body: VerifyBody,
    request: Request,
    response: Response,
    auth: A.CurrentAuth = Depends(A.require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    user = guarded_user(db, request, auth, body)
    record = db.get(TwoFactor, user.id, populate_existing=True)
    if record and record.enabled_at:
        raise APIError(409, "两步验证已启用", code="totp_already_enabled")
    secret = new_secret()
    try:
        encrypted = encrypt_secret(settings.data_dir, secret, create=True)
    except (OSError, ValueError) as error:
        raise APIError(503, "验证器密钥不可用", code="totp_key_unavailable") from error
    if record is None:
        record = TwoFactor(user_id=user.id, secret_ciphertext=encrypted)
        db.add(record)
    record.secret_ciphertext = encrypted
    record.pending_session_id = auth.session_id
    record.pending_expires_at = utcnow() + timedelta(minutes=10)
    record.last_counter = -1
    record.recovery_hashes = []
    uri = (
        "otpauth://totp/"
        + quote("ReelVault:" + user.username, safe="")
        + "?"
        + urlencode(
            {
                "secret": secret,
                "issuer": "ReelVault",
                "algorithm": "SHA1",
                "digits": 6,
                "period": 30,
            }
        )
    )
    qr = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
    stream = io.BytesIO()
    qr.save(stream)
    db.commit()
    return {"secret": secret, "uri": uri, "qr_svg": stream.getvalue().decode("utf-8")}


@router.post("/confirm")
def confirm(
    body: VerifyBody,
    request: Request,
    response: Response,
    auth: A.CurrentAuth = Depends(A.require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    guarded_user(db, request, auth, body)
    record = db.get(TwoFactor, auth.user_id, populate_existing=True)
    if (
        record is None
        or record.enabled_at
        or record.pending_session_id != auth.session_id
        or record.pending_expires_at is None
        or record.pending_expires_at <= utcnow()
    ):
        raise APIError(409, "验证器设置已过期，请重新开始", code="totp_enrollment_expired")
    try:
        secret = decrypt_secret(settings.data_dir, record.secret_ciphertext)
    except (OSError, ValueError) as error:
        raise APIError(503, "验证器密钥不可用", code="totp_key_unavailable") from error
    counter = match_counter(secret, (body.code or "").strip())
    if counter is None:
        request.app.state.totp_limiter.fail(request.client.host if request.client else "unknown")
        raise APIError(403, "验证码无效或已使用", code="totp_invalid")
    record.enabled_at = utcnow()
    record.last_counter = counter
    record.pending_session_id = None
    record.pending_expires_at = None
    codes = new_recovery_codes(record)
    revoke_others(db, auth)
    db.commit()
    return {"recovery_codes": codes}


@router.post("/recovery-codes")
def regenerate(
    body: VerifyBody,
    request: Request,
    response: Response,
    auth: A.CurrentAuth = Depends(A.require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    guarded_user(db, request, auth, body)
    record = db.get(TwoFactor, auth.user_id, populate_existing=True)
    if record is None or not record.enabled_at:
        raise APIError(409, "两步验证尚未启用", code="totp_not_enabled")
    verify_factor(db, request, record, settings, body.code)
    codes = new_recovery_codes(record)
    db.commit()
    return {"recovery_codes": codes}


@router.post("/disable")
def disable(
    body: VerifyBody,
    request: Request,
    response: Response,
    auth: A.CurrentAuth = Depends(A.require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    guarded_user(db, request, auth, body)
    record = db.get(TwoFactor, auth.user_id, populate_existing=True)
    if record and record.enabled_at:
        verify_factor(db, request, record, settings, body.code)
        revoke_others(db, auth)
    if record:
        db.delete(record)
    db.commit()
    return {"ok": True}
