from __future__ import annotations

import mimetypes
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..auth import CurrentAuth, hash_password, hash_token, new_token, require_auth, verify_password
from ..config import Settings
from ..db import get_db
from ..errors import APIError
from ..library import abs_path
from ..media import derive
from ..models import Collection, ShareGrant, ShareLink, Video, utcnow
from ..original_response import original_response
from ..playback_cache import cached_path, needs_copy, stream_path
from ..sharing import (
    PRIVATE_HEADERS,
    cookie_name,
    find_share,
    public_video,
    require_share,
    shared_video,
    shared_videos,
)
from ..storage import lock_budget
from .deps import get_settings

router = APIRouter(tags=["shares"])


class ShareCreate(BaseModel):
    video_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    collection_id: int | None = None
    expires_hours: int = Field(default=168, ge=1, le=8760, strict=True)
    password: str | None = Field(default=None, min_length=6, max_length=256)
    allow_download: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def one_target(self) -> ShareCreate:
        if (self.video_id is None) == (self.collection_id is None):
            raise ValueError("请选择一个视频或一个合集")
        return self


def describe(db: Session, share: ShareLink) -> dict[str, Any]:
    target = (
        db.get(Video, share.video_id) if share.video_id else db.get(Collection, share.collection_id)
    )
    return {
        "title": target.title if isinstance(target, Video) else target.name if target else "",
        "id": share.id,
        "video_id": share.video_id,
        "collection_id": share.collection_id,
        "url": f"/share/{share.token}",
        "expires_at": share.expires_at.isoformat(),
        "password_required": bool(share.password_hash),
        "allow_download": share.allow_download,
        "expired": share.expires_at <= utcnow(),
        "created_at": share.created_at.isoformat(),
    }


@router.post("/api/shares")
def create_share(
    body: ShareCreate,
    auth: CurrentAuth = Depends(require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if body.video_id:
        video = db.get(Video, body.video_id)
        if video is None or video.deleted_at or video.status != "ready":
            raise APIError(404, "视频不存在或尚未就绪", code="share_video_unavailable")
    elif db.get(Collection, body.collection_id) is None:
        raise APIError(404, "合集不存在", code="collection_not_found")
    password_hash = hash_password(body.password) if body.password is not None else None
    lock_budget(db)
    count = (
        db.scalar(
            select(func.count()).select_from(ShareLink).where(ShareLink.owner_id == auth.user_id)
        )
        or 0
    )
    if count >= 200:
        raise APIError(400, "分享链接已达到上限，请先撤销不用的链接", code="share_limit")
    share = ShareLink(
        owner_id=auth.user_id,
        token=new_token(),
        video_id=body.video_id,
        collection_id=body.collection_id,
        expires_at=utcnow() + timedelta(hours=body.expires_hours),
        password_hash=password_hash,
        allow_download=body.allow_download,
    )
    # Preparing expensive media remains an authenticated owner action.
    for item in shared_videos(db, share):
        if needs_copy(item) and cached_path(settings, item) is None:
            raise APIError(409, "请先在视频详情中生成兼容播放缓存", code="playback_cache_required")
    db.add(share)
    db.commit()
    return describe(db, share)


@router.get("/api/shares")
def list_shares(
    auth: CurrentAuth = Depends(require_auth), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return [
        describe(db, share)
        for share in db.scalars(
            select(ShareLink)
            .where(ShareLink.owner_id == auth.user_id)
            .order_by(ShareLink.created_at.desc())
        )
    ]


@router.delete("/api/shares/{share_id}")
def revoke_share(
    share_id: str, auth: CurrentAuth = Depends(require_auth), db: Session = Depends(get_db)
) -> dict[str, bool]:
    share = db.get(ShareLink, share_id)
    if share is None or share.owner_id != auth.user_id:
        raise APIError(404, "分享链接不存在", code="share_unavailable")
    db.delete(share)
    db.commit()
    return {"ok": True}


@router.get("/api/public/shares/{token}")
def view_share(
    token: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    share = find_share(db, token)
    require_share(db, share, request)
    response.headers.update(PRIVATE_HEADERS)
    collection = db.get(Collection, share.collection_id) if share.collection_id else None
    items = [
        {
            **public_video(video, share),
            "playback_ready": not needs_copy(video) or cached_path(settings, video) is not None,
        }
        for video in shared_videos(db, share)
    ]
    return {
        "title": collection.name if collection else items[0]["title"] if items else "",
        "expires_at": share.expires_at.isoformat(),
        "allow_download": share.allow_download,
        "items": items,
    }


class SharePassword(BaseModel):
    password: str = Field(max_length=256)


@router.post("/api/public/shares/{token}/unlock")
def unlock_share(
    token: str,
    body: SharePassword,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, bool]:
    share = find_share(db, token)
    response.headers.update(PRIVATE_HEADERS)
    if not share.password_hash:
        return {"ok": True}
    # Use the actual peer address, not a caller-controlled forwarded header.
    ip = request.client.host if request.client else ""
    limiter = request.app.state.share_limiter
    try:
        limiter.check(ip)
    except APIError as error:
        raise APIError(
            429,
            "分享密码尝试过多，请稍后重试",
            code="share_password_rate_limited",
            params=error.params,
            headers=PRIVATE_HEADERS,
        ) from error
    if not verify_password(share.password_hash, body.password):
        limiter.fail(ip)
        raise APIError(
            403, "分享访问密码不正确", code="share_password_incorrect", headers=PRIVATE_HEADERS
        )
    limiter.success(ip)
    db.execute(delete(ShareGrant).where(ShareGrant.expires_at <= utcnow()))
    grant_token = new_token()
    expires = min(utcnow() + timedelta(hours=24), share.expires_at)
    db.add(ShareGrant(token_hash=hash_token(grant_token), share_id=share.id, expires_at=expires))
    db.commit()
    response.set_cookie(
        cookie_name(share),
        grant_token,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path=f"/api/public/shares/{share.token}",
        max_age=max(1, int((expires - utcnow()).total_seconds())),
    )
    return {"ok": True}


@router.get("/api/public/shares/{token}/videos/{video_id}/{resource}")
def share_media(
    token: str,
    video_id: str,
    resource: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    share = find_share(db, token)
    require_share(db, share, request)
    video = shared_video(db, share, video_id)
    filename = None
    original: str | None = None
    if resource == "stream":
        path = stream_path(settings, video)
        if path == abs_path(settings, video.file_path):
            original = video.file_path
        media_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
    elif resource == "poster.jpg":
        path = settings.derived_dir / video.id / derive.POSTER
        media_type = "image/jpeg"
    elif resource == "download":
        if not share.allow_download:
            raise APIError(
                403,
                "此分享不允许下载原文件",
                code="share_download_disabled",
                headers=PRIVATE_HEADERS,
            )
        path = abs_path(settings, video.file_path)
        original = video.file_path
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        filename = video.title + path.suffix
    else:
        raise APIError(
            404, "分享资源不存在", code="share_video_unavailable", headers=PRIVATE_HEADERS
        )
    if original is None and not path.is_file():
        raise APIError(
            404, "分享视频文件不可用", code="share_video_unavailable", headers=PRIVATE_HEADERS
        )
    return original_response(
        request,
        settings,
        original,
        path,
        media_type=media_type,
        filename=filename,
        headers=PRIVATE_HEADERS,
    )
