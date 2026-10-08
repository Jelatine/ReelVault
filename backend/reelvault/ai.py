"""Private AI capabilities, strict model outputs and source-aware analysis state."""

from __future__ import annotations

import math
import struct
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, exists
from sqlalchemy.orm import Session

from .config import Settings
from .errors import APIError
from .models import AiAnalysis, FaceGroup, FaceObservation, RuntimeSetting, Video, VideoVectorIndex
from .vision import MODEL_ID, VisionClient, check_enabled, vector
from .visual_search import current

FACE_MODEL_ID = "yunet-sface-4f3f83ee562f91109d0098a7"
DEFAULT_LABELS = [
    "海滩",
    "日落",
    "山景",
    "森林",
    "城市街道",
    "建筑",
    "室内",
    "花园",
    "大海",
    "湖泊",
    "雪景",
    "运动",
    "舞台表演",
    "食物",
    "车辆",
    "动物",
    "人群",
    "人物肖像",
    "夜景",
    "彩色测试图案",
]


class AiParams(BaseModel):
    model_config = ConfigDict(strict=True)
    candidates: list[str] = Field(
        default_factory=lambda: list(DEFAULT_LABELS), min_length=1, max_length=32
    )
    min_score: float = Field(0.25, ge=0.1, le=1, allow_inf_nan=False)
    max_tags: int = Field(5, ge=1, le=10)
    faces: bool = False
    face_threshold: float = Field(0.65, ge=0.4, le=0.95, allow_inf_nan=False)
    priority: int = Field(1, ge=0, le=2)

    @field_validator("candidates")
    @classmethod
    def labels(cls, labels: list[str]) -> list[str]:
        result = [label.strip() for label in labels]
        if any(
            not label or len(label) > 64 or any(ord(c) < 32 for c in label) for label in result
        ) or len(set(result)) != len(result):
            raise ValueError("Use 1–32 unique labels of 1–64 characters")
        return result


def check_ai(settings: Settings, *, faces: bool = False) -> None:
    if not settings.ai_enabled:
        raise APIError(403, "AI 分析尚未启用", code="ai_disabled")
    check_enabled(settings)
    if faces and not settings.ai_faces_enabled:
        raise APIError(403, "人脸分组尚未启用", code="ai_faces_disabled")


def current_analysis(
    settings: Settings, video: Video, index: VideoVectorIndex, analysis: AiAnalysis
) -> bool:
    return (
        analysis.model == MODEL_ID
        and analysis.generation == index.generation
        and current(settings, video, index)
    )


def revision(db: Session, *, advance: bool = False) -> int:
    record = db.get(RuntimeSetting, "ai-face-revision")
    value = int(record.value.get("version", 0)) if record else 0
    if advance:
        if record is None:
            record = RuntimeSetting(key="ai-face-revision", value={})
            db.add(record)
        record.value = {"version": value + 1}
    return value


def prune_groups(db: Session) -> None:
    db.execute(
        delete(FaceGroup).where(
            FaceGroup.name == "", ~exists().where(FaceObservation.group_id == FaceGroup.id)
        )
    )


def request(
    client: VisionClient, route: str, data: bytes, content_type: str = "application/json"
) -> dict[str, Any]:
    for attempt in range(61):
        try:
            return client.request(route, data, content_type)
        except APIError as error:
            if error.code != "vision_busy" or attempt == 60:
                raise
            time.sleep(1)
    raise AssertionError("Unreachable")


def face_values(face: Any) -> dict[str, Any]:
    if not isinstance(face, dict):
        raise ValueError("Invalid face record")
    box, score = face.get("box"), face.get("score")
    if (
        not isinstance(box, list)
        or len(box) != 4
        or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in box
        )
        or not (0 <= box[0] < 1 and 0 <= box[1] < 1 and 0 < box[2] <= 1 and 0 < box[3] <= 1)
        or box[0] + box[2] > 1.000001
        or box[1] + box[3] > 1.000001
        or isinstance(score, bool)
        or not isinstance(score, (float, int))
        or not math.isfinite(score)
        or not 0.85 <= score <= 1
    ):
        raise ValueError("Invalid face bounds or confidence")
    return {"box": box, "score": score, "vector": vector(face.get("vector"), 128)}


def face_embedding(values: Any) -> bytes:
    return struct.pack("<128f", *vector(values, 128))
