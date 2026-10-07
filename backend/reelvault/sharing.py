"""Share authorization never confers an authenticated library session."""

from __future__ import annotations

import re
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .auth import hash_token
from .errors import APIError
from .models import Collection, CollectionItem, ShareGrant, ShareLink, Video, utcnow

TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")
PRIVATE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}


def cookie_name(share: ShareLink) -> str:
    return f"rv_share_{share.id}"


def find_share(db: Session, token: str) -> ShareLink:
    share = (
        db.scalar(select(ShareLink).where(ShareLink.token == token))
        if TOKEN.fullmatch(token)
        else None
    )
    if share is None or share.expires_at <= utcnow():
        raise APIError(
            404, "分享链接不存在、已撤销或已过期", code="share_unavailable", headers=PRIVATE_HEADERS
        )
    if share.video_id:
        video = db.get(Video, share.video_id)
        if video is None or video.deleted_at is not None:
            raise APIError(404, "分享链接不可用", code="share_unavailable", headers=PRIVATE_HEADERS)
    elif db.get(Collection, share.collection_id) is None:
        raise APIError(404, "分享链接不可用", code="share_unavailable", headers=PRIVATE_HEADERS)
    return share


def require_share(db: Session, share: ShareLink, request: Request) -> None:
    if not share.password_hash:
        return
    token = request.cookies.get(cookie_name(share))
    grant = db.get(ShareGrant, hash_token(token)) if token and len(token) <= 128 else None
    if grant is None or grant.share_id != share.id or grant.expires_at <= utcnow():
        # 403 avoids confusing a password-protected share with admin session expiry.
        raise APIError(
            403, "请输入分享访问密码", code="share_password_required", headers=PRIVATE_HEADERS
        )


def shared_videos(db: Session, share: ShareLink) -> list[Video]:
    statement = select(Video).where(Video.deleted_at.is_(None), Video.status == "ready")
    if share.video_id:
        statement = statement.where(Video.id == share.video_id)
    else:
        statement = (
            statement.join(CollectionItem, CollectionItem.video_id == Video.id)
            .where(CollectionItem.collection_id == share.collection_id)
            .order_by(CollectionItem.position, CollectionItem.video_id)
        )
    return list(db.scalars(statement))


def shared_video(db: Session, share: ShareLink, video_id: str) -> Video:
    statement = select(Video).where(
        Video.id == video_id, Video.deleted_at.is_(None), Video.status == "ready"
    )
    if share.video_id:
        statement = statement.where(Video.id == share.video_id)
    else:
        statement = statement.join(CollectionItem, CollectionItem.video_id == Video.id).where(
            CollectionItem.collection_id == share.collection_id
        )
    video = db.scalar(statement)
    if video is None:
        raise APIError(
            404,
            "该视频不在分享范围内或尚未就绪",
            code="share_video_unavailable",
            headers=PRIVATE_HEADERS,
        )
    return video


def public_video(video: Video, share: ShareLink) -> dict[str, Any]:
    # Explicit allowlist: no GPS, source paths, history, library tags or admin URLs.
    prefix = f"/api/public/shares/{share.token}/videos/{video.id}"
    return {
        "id": video.id,
        "title": video.title,
        "duration": video.duration,
        "width": video.width,
        "height": video.height,
        "stream_url": prefix + "/stream",
        "poster_url": prefix + "/poster.jpg" if video.has_poster else None,
        "download_url": prefix + "/download" if share.allow_download else None,
    }


class SharePrivacyMiddleware:
    """Apply privacy headers to share validation errors and unavailable disks too."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not (
            scope["path"].startswith("/api/public/shares/")
            or scope["path"] == "/api/shares"
            or scope["path"].startswith("/api/shares/")
            or scope["path"].startswith("/share/")
        ):
            await self.app(scope, receive, send)
            return

        async def private_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = {
                    key.lower().encode(): value.encode() for key, value in PRIVATE_HEADERS.items()
                }
                message = {
                    **message,
                    "headers": [
                        (key, value)
                        for key, value in message["headers"]
                        if key.lower() not in headers
                    ]
                    + list(headers.items()),
                }
            await send(message)

        await self.app(scope, receive, private_send)
