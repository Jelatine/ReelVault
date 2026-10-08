"""Credential-free, immutable identities shared by storage and local consumers."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path, PosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator

KEY = re.compile(r"[a-f0-9]{32}(?:-[a-f0-9]{32})?\.[a-z0-9]{1,10}")


class ObjectRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str
    size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    etag: str = Field(min_length=1, max_length=1024)
    version_id: str | None = Field(default=None, max_length=1024)
    modified: datetime
    transfer_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")

    @field_validator("key")
    @classmethod
    def safe_key(cls, value: str) -> str:
        if KEY.fullmatch(value) is None:
            raise ValueError("Invalid original-video object key")
        return value


class OriginalPath(PosixPath):
    """A real cache path with a stable remote identity; stat remains a real stat."""

    object_ref: ObjectRef


def original_path(path: Path, ref: ObjectRef) -> OriginalPath:
    result = OriginalPath(path)
    result.object_ref = ref
    return result
