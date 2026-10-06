from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Request, Response, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import Settings
from .errors import APIError
from .models import AuthSession, User, utcnow

COOKIE_NAME = "rv_session"
# A rotated-out token keeps working this long, so in-flight requests don't fail.
ROTATION_GRACE = timedelta(minutes=2)
# Avoid a DB write on every request.
TOUCH_INTERVAL = timedelta(seconds=60)

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def session_lifetime(settings: Settings, remember: bool) -> timedelta:
    if remember:
        return timedelta(days=settings.remember_days)
    return timedelta(hours=settings.session_idle_hours)


def set_session_cookie(response: Response, settings: Settings, token: str, remember: bool) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        # Without "remember me" this is a browser-session cookie.
        max_age=settings.remember_days * 86400 if remember else None,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=settings.secure_cookies
    )


def create_session(
    db: Session,
    settings: Settings,
    user: User,
    request: Request,
    remember: bool,
    device_name: str,
) -> tuple[AuthSession, str]:
    token = new_token()
    now = utcnow()
    sess = AuthSession(
        user_id=user.id,
        token_hash=hash_token(token),
        device_name=device_name[:128],
        user_agent=request.headers.get("user-agent", "")[:512],
        ip=client_ip(request),
        remember=remember,
        created_at=now,
        last_seen_at=now,
        rotated_at=now,
        expires_at=now + session_lifetime(settings, remember),
    )
    db.add(sess)
    db.commit()
    return sess, token


def lookup_session(db: Session, token: str | None) -> AuthSession | None:
    if not token:
        return None
    h = hash_token(token)
    sess = db.scalar(
        select(AuthSession).where(
            or_(AuthSession.token_hash == h, AuthSession.prev_token_hash == h)
        )
    )
    if sess is None:
        return None
    now = utcnow()
    if sess.expires_at <= now:
        db.delete(sess)
        db.commit()
        return None
    if sess.token_hash != h and now - sess.rotated_at > ROTATION_GRACE:
        return None
    return sess


def touch_session(db: Session, settings: Settings, sess: AuthSession, request: Request) -> None:
    """Slide the expiry window forward and record activity."""
    now = utcnow()
    if now - sess.last_seen_at < TOUCH_INTERVAL:
        return
    sess.last_seen_at = now
    sess.ip = client_ip(request)
    sess.expires_at = now + session_lifetime(settings, sess.remember)
    db.commit()


def maybe_rotate(db: Session, settings: Settings, sess: AuthSession, response: Response) -> None:
    """Rotate long-lived tokens periodically and refresh the cookie expiry."""
    if not sess.remember:
        return
    if utcnow() - sess.rotated_at < timedelta(days=settings.token_rotate_days):
        return
    token = new_token()
    sess.prev_token_hash = sess.token_hash
    sess.token_hash = hash_token(token)
    sess.rotated_at = utcnow()
    db.commit()
    set_session_cookie(response, settings, token, remember=True)


@dataclass
class CurrentAuth:
    user_id: int
    username: str
    session_id: str
    remember: bool


def require_auth(request: Request) -> CurrentAuth:
    """Dependency: resolve the session cookie. Uses its own short-lived DB session
    so it also works for streaming responses."""
    settings: Settings = request.app.state.settings
    with request.app.state.sessionmaker() as db:
        sess = lookup_session(db, request.cookies.get(COOKIE_NAME))
        if sess is None:
            raise APIError(
                status.HTTP_401_UNAUTHORIZED, "未登录或登录已过期", code="authentication_required"
            )
        user = db.get(User, sess.user_id)
        if user is None:
            raise APIError(status.HTTP_401_UNAUTHORIZED, "用户不存在", code="user_not_found")
        touch_session(db, settings, sess, request)
        auth = CurrentAuth(user.id, user.username, sess.id, sess.remember)
    request.state.auth = auth
    return auth


class LoginLimiter:
    """In-memory per-IP lockout after repeated failed logins."""

    def __init__(self, max_failures: int, lock_seconds: int) -> None:
        self.max_failures = max_failures
        self.lock_seconds = lock_seconds
        self._state: dict[str, tuple[int, float]] = {}

    def check(self, ip: str) -> None:
        failures, locked_until = self._state.get(ip, (0, 0.0))
        remaining = locked_until - time.monotonic()
        if remaining > 0:
            raise APIError(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"登录失败次数过多，请 {int(remaining // 60) + 1} 分钟后再试",
                code="login_rate_limited",
                params={"minutes": int(remaining // 60) + 1},
            )

    def fail(self, ip: str) -> None:
        failures, _ = self._state.get(ip, (0, 0.0))
        failures += 1
        locked_until = 0.0
        if failures >= self.max_failures:
            locked_until = time.monotonic() + self.lock_seconds
            failures = 0
        self._state[ip] = (failures, locked_until)

    def success(self, ip: str) -> None:
        self._state.pop(ip, None)


def purge_expired_sessions(db: Session) -> int:
    expired = db.scalars(select(AuthSession).where(AuthSession.expires_at <= utcnow())).all()
    for s in expired:
        db.delete(s)
    db.commit()
    return len(expired)
