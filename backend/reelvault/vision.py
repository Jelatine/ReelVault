"""Bounded private CLIP protocol and source-aware sampling."""

from __future__ import annotations

import json
import math
import struct
import time
import urllib.error
import urllib.request
from typing import Any

from pydantic import BaseModel, Field

from .config import Settings
from .errors import APIError

MODEL_ID = "clip-b32-multilingual-473813d255b5300eb3038d7f"
DIMENSION = 512
MAX_IMAGE_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 256 * 1024


class VisionParams(BaseModel):
    interval: float = Field(30, ge=1, le=3600, allow_inf_nan=False)
    max_frames: int = Field(240, ge=1, le=1000)
    priority: int = Field(1, ge=0, le=2)


def check_enabled(settings: Settings) -> None:
    if not settings.vision_enabled:
        raise APIError(403, "画面搜索尚未启用", code="vision_disabled")


def sample_times(duration: float, interval: float, maximum: int, start: float = 0) -> list[float]:
    if not all(math.isfinite(x) for x in (duration, interval, start)) or not (
        0 <= start < duration <= 24 * 3600 and interval >= 1 and 1 <= maximum <= 1000
    ):
        raise ValueError("Invalid sampling range")
    span = duration - start
    count = min(maximum, math.ceil(span / interval))
    step = max(interval, span / count)
    return [round(start + i * step, 6) for i in range(count)]


def vector(values: Any) -> list[float]:
    if (
        not isinstance(values, list)
        or len(values) != DIMENSION
        or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in values
        )
    ):
        raise ValueError("Invalid vision vector")
    norm = math.sqrt(sum(v * v for v in values))
    if not math.isfinite(norm) or not 0.99 <= norm <= 1.01:
        raise ValueError("Vision vector is not normalized")
    return [v / norm for v in values]


def packed(values: Any) -> bytes:
    return struct.pack(f"<{DIMENSION}f", *vector(values))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class VisionClient:
    def __init__(self, url: str, token: str):
        self.url, self.token = url.rstrip("/"), token
        # A private inference request must not inherit a machine's external proxy.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(
        self, route: str, data: bytes | None = None, content_type: str = "application/json"
    ) -> dict[str, Any]:
        req = urllib.request.Request(
            self.url + route,
            data=data,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": content_type},
        )
        try:
            with self.opener.open(req, timeout=5 if route == "/health" else 30) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            if error.code == 503:
                raise APIError(
                    503, "视觉模型正在处理其他任务，请稍后重试", code="vision_busy"
                ) from error
            raise APIError(
                502, "视觉服务拒绝请求，请检查部署令牌与服务日志", code="vision_service_failed"
            ) from error
        except (OSError, urllib.error.URLError) as error:
            raise APIError(
                503, "视觉服务无法连接，请检查部署配置", code="vision_unavailable"
            ) from error
        try:
            result = json.loads(raw)
            if (
                len(raw) > MAX_RESPONSE_BYTES
                or not isinstance(result, dict)
                or (
                    result.get("protocol") != 1
                    or result.get("model") != MODEL_ID
                    or result.get("dimension") != DIMENSION
                )
            ):
                raise ValueError("Incompatible service")
            return result
        except (ValueError, TypeError) as error:
            raise APIError(
                502, "视觉服务模型或响应不兼容", code="vision_protocol_invalid"
            ) from error

    def embedding(
        self,
        route: str,
        data: bytes,
        content_type: str = "application/json",
        *,
        retries: bool = False,
    ) -> list[float]:
        for attempt in range(61 if retries else 1):
            try:
                result = self.request(route, data, content_type)
                vectors = result.get("vectors")
                if not isinstance(vectors, list) or len(vectors) != 1:
                    raise ValueError("Expected one vector")
                return vector(vectors[0])
            except APIError as error:
                if retries and error.code == "vision_busy" and attempt < 60:
                    time.sleep(1)
                    continue
                raise
            except (ValueError, TypeError) as error:
                raise APIError(
                    502, "视觉服务返回非法向量", code="vision_protocol_invalid"
                ) from error
        raise AssertionError("Unreachable")

    def text(self, text: str) -> list[float]:
        if not text.strip() or len(text) > 512:
            raise APIError(400, "请输入 1–512 字符的画面描述", code="vision_text_invalid")
        return self.embedding("/embed/text", json.dumps({"texts": [text]}).encode())

    def image(self, data: bytes, *, retries: bool = False) -> list[float]:
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise APIError(400, "查询图片大小不符合要求", code="vision_image_invalid")
        return self.embedding("/embed/image", data, "image/jpeg", retries=retries)
