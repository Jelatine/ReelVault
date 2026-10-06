from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from ..config import Settings
from ..library import abs_path
from ..models import MediaAsset
from .ops import AdjustParams, AudioParams, SubtitleParams, WatermarkParams


class AssetError(ValueError):
    pass


def audio_asset(db: Session, settings: Settings, params: AudioParams) -> MediaAsset | None:
    if params.mode == "adjust":
        return None
    return checked_asset(db, settings, params.audio_asset_id, "audio")


def subtitle_asset(db: Session, settings: Settings, params: SubtitleParams) -> MediaAsset | None:
    if params.subtitle_asset_id is None:
        return None
    return checked_asset(db, settings, params.subtitle_asset_id, "subtitle")


def checked_asset(db: Session, settings: Settings, asset_id: str | None, kind: str) -> MediaAsset:
    label = {"audio": "音频素材", "subtitle": "字幕素材", "image": "图片素材", "lut": "LUT 素材"}[
        kind
    ]
    asset = db.get(MediaAsset, asset_id)
    if asset is None or asset.kind != kind:
        raise AssetError(f"{label}不存在")
    path = abs_path(settings, asset.file_path)
    if not path.is_file():
        raise AssetError(f"{label}文件缺失")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != asset.sha256:
        raise AssetError(f"{label}文件已变化，请重新上传")
    return asset


def image_asset(db: Session, settings: Settings, params: WatermarkParams) -> MediaAsset | None:
    if params.mode == "text":
        return None
    return checked_asset(db, settings, params.image_asset_id, "image")


def references(value: object, asset_id: str) -> bool:
    if isinstance(value, dict):
        return any(references(item, asset_id) for item in value.values())
    if isinstance(value, list):
        return any(references(item, asset_id) for item in value)
    return value == asset_id


def lut_asset(db: Session, settings: Settings, params: AdjustParams) -> MediaAsset | None:
    if params.lut_asset_id is None:
        return None
    return checked_asset(db, settings, params.lut_asset_id, "lut")
