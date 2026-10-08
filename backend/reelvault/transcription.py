from __future__ import annotations

import html
import importlib.util
import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .config import Settings
from .content_search import MAX_BYTES, MAX_CUES
from .errors import APIError
from .models import Video


class TranscriptionParams(BaseModel):
    language: Literal["auto", "zh", "en", "ja", "ko", "fr", "de", "es", "ru", "pt", "ar", "it"] = (
        "auto"
    )
    priority: int = Field(1, ge=0, le=2, strict=True)


def available(settings: Settings) -> bool:
    return settings.transcription_enabled and importlib.util.find_spec("faster_whisper") is not None


def estimate(video: Video) -> int:
    return math.ceil(video.duration * 32000) + 32 * 1024 * 1024


def check_enabled(settings: Settings, video: Video) -> None:
    if not settings.transcription_enabled:
        raise APIError(409, "管理员尚未启用本地语音转写", code="transcription_disabled")
    if not available(settings):
        raise APIError(
            409, "请安装可选 transcription 依赖", code="transcription_dependency_missing"
        )
    if not video.audio_codec:
        raise APIError(400, "视频没有音轨", code="transcription_no_audio")
    if (
        not math.isfinite(video.duration)
        or not 0 < video.duration <= settings.transcription_max_hours * 3600
    ):
        raise APIError(
            400,
            "视频超过部署设置的转写时长上限",
            code="transcription_duration_limit",
            params={"hours": settings.transcription_max_hours},
        )


def vtt_timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, rest = divmod(millis, 3600000)
    minutes, rest = divmod(rest, 60000)
    secs, rest = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}.{rest:03}"


def read_result(path: Path, duration: float) -> tuple[bytes, str, int]:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("转写结果超过 5 MiB")
    result: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict) or not isinstance(result.get("cues"), list):
        raise ValueError("转写结果格式无效")
    if len(result["cues"]) > MAX_CUES:
        raise ValueError("字幕片段超过 50000 个")
    language = result.get("language")
    if (
        not isinstance(language, str)
        or not language.isascii()
        or not language.isalpha()
        or not 2 <= len(language) <= 8
    ):
        raise ValueError("转写语言格式无效")
    lines = ["WEBVTT", ""]
    count = 0
    previous = -1.0
    for cue in result["cues"]:
        if not isinstance(cue, dict) or not isinstance(cue.get("text"), str):
            raise ValueError("字幕片段格式无效")
        start: Any = cue.get("start")
        end: Any = cue.get("end")
        if any(
            type(value) not in (int, float) or not math.isfinite(value) for value in (start, end)
        ):
            raise ValueError("字幕时间格式无效")
        if start < previous or start < 0 or end <= start or start >= duration or end > duration + 1:
            raise ValueError("字幕时间超出源视频范围")
        previous = start
        value = " ".join(cue["text"].split())
        if len(value) > 8192:
            raise ValueError("字幕单句过长")
        if not value:
            continue
        end = min(duration, end)
        if round(end * 1000) <= round(start * 1000):
            continue
        lines += [f"{vtt_timestamp(start)} --> {vtt_timestamp(end)}", html.escape(value), ""]
        count += 1
    data = "\n".join(lines).encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ValueError("转写字幕超过 5 MiB")
    return data, language.lower(), count
