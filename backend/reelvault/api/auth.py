from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import auth as A
from ..config import Settings
from ..db import get_db
from ..errors import APIError
from ..models import AuthSession, TwoFactor, User, utcnow
from ..observability import audit
from ..storage import lock_budget
from ..two_factor import consume
from .deps import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SetupBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=256)


class LoginBody(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)
    remember: bool = False
    device_name: str = Field("", max_length=128)
    code: str | None = Field(None, max_length=64)


class PasswordBody(BaseModel):
    current_password: str = Field(max_length=256)
    code: str | None = Field(None, max_length=64)
    new_password: str = Field(min_length=6, max_length=256)
    logout_others: bool = True


class RenameBody(BaseModel):
    device_name: str = Field(min_length=1, max_length=128)


def _user_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User)) or 0


def _session_dict(s: AuthSession, current_id: str) -> dict[str, Any]:
    return {
        "id": s.id,
        "device_name": s.device_name,
        "user_agent": s.user_agent,
        "ip": s.ip,
        "remember": s.remember,
        "created_at": s.created_at.isoformat(),
        "last_seen_at": s.last_seen_at.isoformat(),
        "expires_at": s.expires_at.isoformat(),
        "current": s.id == current_id,
    }


@router.get("/status")
def auth_status(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    sess = A.lookup_session(db, request.cookies.get(A.COOKIE_NAME))
    return {"setup_required": _user_count(db) == 0, "authenticated": sess is not None}


@router.post("/setup")
def setup(
    body: SetupBody,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if _user_count(db) > 0:
        raise APIError(status.HTTP_409_CONFLICT, "管理员账号已存在", code="admin_exists")
    user = User(username=body.username.strip(), password_hash=A.hash_password(body.password))
    db.add(user)
    db.commit()
    audit(
        db,
        settings,
        "login_success",
        actor=user.username,
        peer=request.client.host if request.client else "",
    )
    _, token = A.create_session(db, settings, user, request, True, "初始化设备")
    A.set_session_cookie(response, settings, token, remember=True)
    return {"username": user.username}


@router.post("/login")
def login(
    body: LoginBody,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    limiter: A.LoginLimiter = request.app.state.login_limiter
    ip = A.client_ip(request)
    peer = request.client.host if request.client else ""

    def failure(event: str, reason: str) -> None:
        audit(
            db, settings, event, actor=body.username.strip(), peer=peer, details={"reason": reason}
        )
        db.commit()

    try:
        limiter.check(ip)
        lock_budget(db)
        limiter.check(ip)
    except APIError:
        failure("login_limited", "password_rate_limit")
        raise
    user = db.scalar(select(User).where(User.username == body.username.strip()))
    if user is None or not A.verify_password(user.password_hash, body.password):
        limiter.fail(ip)
        failure("login_failure", "credentials")
        raise APIError(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误", code="invalid_credentials")
    factor = db.get(TwoFactor, user.id)
    if factor is not None and factor.enabled_at:
        try:
            request.app.state.totp_limiter.check(peer)
            consume(factor, settings.data_dir, body.code)
        except APIError as error:
            if error.code == "totp_invalid":
                request.app.state.totp_limiter.fail(peer)
            if error.code != "totp_required":
                failure(
                    "login_limited" if error.status_code == 429 else "login_failure",
                    error.code or "second_factor",
                )
            raise
    limiter.success(ip)
    device = body.device_name.strip() or "未命名设备"
    audit(db, settings, "login_success", actor=user.username, peer=peer)
    sess, token = A.create_session(db, settings, user, request, body.remember, device)
    A.set_session_cookie(response, settings, token, remember=body.remember)
    return {"username": user.username, "session_id": sess.id, "remember": sess.remember}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, bool]:
    sess = A.lookup_session(db, request.cookies.get(A.COOKIE_NAME))
    if sess is not None:
        user = db.get(User, sess.user_id)
        audit(
            db,
            settings,
            "logout",
            actor=user.username if user else "unknown",
            peer=request.client.host if request.client else "",
        )
        db.delete(sess)
        db.commit()
    A.clear_session_cookie(response, settings)
    return {"ok": True}


@router.get("/me")
def me(
    request: Request,
    response: Response,
    auth: A.CurrentAuth = Depends(A.require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    sess = db.get(AuthSession, auth.session_id)
    if sess is not None:
        A.maybe_rotate(db, settings, sess, response)
    return {"username": auth.username, "session_id": auth.session_id, "remember": auth.remember}


@router.post("/password")
def change_password(
    body: PasswordBody,
    request: Request,
    auth: A.CurrentAuth = Depends(A.require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    lock_budget(db)
    user = db.get(User, auth.user_id, populate_existing=True)
    assert user is not None
    if not A.verify_password(user.password_hash, body.current_password):
        raise APIError(
            status.HTTP_400_BAD_REQUEST, "当前密码不正确", code="current_password_incorrect"
        )
    factor = db.get(TwoFactor, user.id)
    if factor and factor.enabled_at:
        peer = request.client.host if request.client else "unknown"
        request.app.state.totp_limiter.check(peer)
        try:
            consume(factor, settings.data_dir, body.code)
        except APIError as error:
            if error.code == "totp_invalid":
                request.app.state.totp_limiter.fail(peer)
            raise
    elif factor:
        db.delete(factor)
    user.password_hash = A.hash_password(body.new_password)
    user.password_changed_at = utcnow()
    revoked = 0
    if body.logout_others:
        others = db.scalars(
            select(AuthSession).where(
                AuthSession.user_id == user.id, AuthSession.id != auth.session_id
            )
        ).all()
        for s in others:
            db.delete(s)
        revoked = len(others)
    db.commit()
    return {"ok": True, "revoked": revoked}


@router.get("/sessions")
def list_sessions(
    auth: A.CurrentAuth = Depends(A.require_auth), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    A.purge_expired_sessions(db)
    rows = db.scalars(
        select(AuthSession)
        .where(AuthSession.user_id == auth.user_id)
        .order_by(AuthSession.last_seen_at.desc())
    ).all()
    return [_session_dict(s, auth.session_id) for s in rows]


@router.patch("/sessions/{session_id}")
def rename_session(
    session_id: str,
    body: RenameBody,
    auth: A.CurrentAuth = Depends(A.require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    sess = db.get(AuthSession, session_id)
    if sess is None or sess.user_id != auth.user_id:
        raise APIError(status.HTTP_404_NOT_FOUND, "会话不存在", code="session_not_found")
    sess.device_name = body.device_name.strip()
    db.commit()
    return _session_dict(sess, auth.session_id)


@router.delete("/sessions/{session_id}")
def revoke_session(
    session_id: str,
    response: Response,
    auth: A.CurrentAuth = Depends(A.require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, bool]:
    sess = db.get(AuthSession, session_id)
    if sess is None or sess.user_id != auth.user_id:
        raise APIError(status.HTTP_404_NOT_FOUND, "会话不存在", code="session_not_found")
    db.delete(sess)
    db.commit()
    if session_id == auth.session_id:
        A.clear_session_cookie(response, settings)
    return {"ok": True}


@router.post("/sessions/revoke-others")
def revoke_others(
    auth: A.CurrentAuth = Depends(A.require_auth), db: Session = Depends(get_db)
) -> dict[str, int]:
    others = db.scalars(
        select(AuthSession).where(
            AuthSession.user_id == auth.user_id, AuthSession.id != auth.session_id
        )
    ).all()
    for s in others:
        db.delete(s)
    db.commit()
    return {"revoked": len(others)}
