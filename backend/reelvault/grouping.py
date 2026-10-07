"""Live virtual classifications; no persisted membership or physical file moves."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import and_, func, or_

from .errors import APIError
from .models import Video


def parse_group(key: str) -> tuple[str, str]:
    kind, _, value = key.partition(":")
    valid = (
        (kind == "year" and re.fullmatch(r"[0-9]{4}", value) and 1 <= int(value) <= 9999)
        or (
            kind == "month"
            and re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", value)
            and int(value[:4]) > 0
        )
        or (kind == "device" and (value == "unknown" or re.fullmatch(r"[0-9a-f]{64}", value)))
        or (kind == "resolution" and value in {"portrait", "landscape", "square", "4k", "unknown"})
        or (kind == "date" and value == "unknown")
    )
    if not valid:
        raise APIError(400, "无效的自动分组条件", code="auto_group_invalid")
    return kind, value


def group_condition(key: str) -> Any:
    kind, value = parse_group(key)
    if kind == "year":
        return func.strftime("%Y", Video.captured_at) == value
    if kind == "month":
        return func.strftime("%Y-%m", Video.captured_at) == value
    if kind == "date":
        return Video.captured_at.is_(None)
    if kind == "device":
        return func.reelvault_device(Video.meta, Video.metadata_overrides) == value
    valid_size = and_(Video.width > 0, Video.height > 0)
    if value == "unknown":
        return or_(Video.width <= 0, Video.height <= 0)
    condition = {
        "portrait": Video.height > Video.width,
        "landscape": Video.width > Video.height,
        "square": Video.width == Video.height,
        "4k": func.min(Video.width, Video.height) >= 2160,
    }[value]
    return and_(valid_size, condition)
