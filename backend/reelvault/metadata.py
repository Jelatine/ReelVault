"""Readable capture metadata and explicit library-only corrections."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import Video


def capture_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        date = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return date.replace(tzinfo=UTC) if date.tzinfo is None else date.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def source_metadata(tags: dict[str, Any]) -> dict[str, Any]:
    tags = {key.lower(): value for key, value in tags.items()}

    def first(*keys: str) -> str | None:
        for key in keys:
            value = tags.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    captured = None
    for key in (
        "com.apple.quicktime.creationdate",
        "creationdate",
        "creation_time",
        "date_time_original",
    ):
        captured = capture_time(tags.get(key))
        if captured is not None:
            break
    location = first(
        "com.apple.quicktime.location.iso6709", "location.iso6709", "location", "gpscoordinates"
    )
    gps = None
    if location:
        # Common QuickTime decimal degrees with optional altitude, ISO 6709.
        match = re.fullmatch(
            r"([+-]\d{2}(?:\.\d+)?)([+-]\d{3}(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?/?", location
        )
        if match:
            latitude, longitude = float(match[1]), float(match[2])
            altitude = float(match[3]) if match[3] else None
            if (
                -90 <= latitude <= 90
                and -180 <= longitude <= 180
                and (altitude is None or math.isfinite(altitude))
            ):
                gps = {"latitude": latitude, "longitude": longitude, "altitude": altitude}
    return {
        "captured_at": captured.isoformat() if captured else None,
        "device_make": first("com.apple.quicktime.make", "make", "manufacturer"),
        "device_model": first("com.apple.quicktime.model", "model", "camera_model", "device.model"),
        "gps": gps,
    }


def metadata_values(video: Video) -> dict[str, Any]:
    source = source_metadata(video.meta or {})
    overrides = video.metadata_overrides or {}
    return {
        **{key: overrides.get(key, source[key]) for key in ("device_make", "device_model", "gps")},
        "custom_fields": video.custom_fields or {},
        "overridden": sorted(overrides),
    }


def device_key(raw_meta: str | None, raw_overrides: str | None) -> str:
    """SQLite's deterministic device classifier uses the same effective metadata as the UI."""
    tags = json.loads(raw_meta or "{}") or {}
    overrides = json.loads(raw_overrides or "{}") or {}
    source = source_metadata(tags)
    values = [overrides.get(key, source[key]) for key in ("device_make", "device_model")]
    if not any(values):
        return "unknown"
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class GPS(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude: float | None = None


class MetadataPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    captured_at: datetime | None = None
    device_make: str | None = Field(None, max_length=128)
    device_model: str | None = Field(None, max_length=128)
    gps: GPS | None = None
    custom_fields: dict[str, str] | None = None
    reset: bool = False

    @field_validator("device_make", "device_model")
    @classmethod
    def trim(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("custom_fields")
    @classmethod
    def valid_fields(cls, fields: dict[str, str] | None) -> dict[str, str]:
        if fields is None or len(fields) > 50:
            raise ValueError("Supply at most fifty custom fields")
        clean = {}
        for name, value in fields.items():
            key = name.strip()
            if not key or len(key) > 64 or len(value) > 1000 or key in clean:
                raise ValueError("Custom field names must be unique and within length limits")
            clean[key] = value
        return clean

    @model_validator(mode="after")
    def valid_reset(self) -> MetadataPatch:
        if self.reset and self.model_fields_set - {"reset"}:
            raise ValueError("Reset cannot be combined with corrections")
        return self


def update_metadata(video: Video, body: MetadataPatch) -> None:
    if body.reset:
        video.metadata_overrides = {}
        video.captured_at = capture_time(source_metadata(video.meta or {})["captured_at"])
        return
    overrides = dict(video.metadata_overrides or {})
    values = body.model_dump(mode="json")
    for key in ("captured_at", "device_make", "device_model", "gps"):
        if key in body.model_fields_set:
            overrides[key] = values[key]
    video.metadata_overrides = overrides
    if "captured_at" in body.model_fields_set:
        date = body.captured_at
        video.captured_at = (
            (date.replace(tzinfo=UTC) if date.tzinfo is None else date.astimezone(UTC))
            if date
            else None
        )
    if body.custom_fields is not None:
        video.custom_fields = body.custom_fields
