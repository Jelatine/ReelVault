from __future__ import annotations

import math
from typing import Annotated, Any

from fastapi import Request
from pydantic import BeforeValidator

from ..config import Settings
from ..errors import APIError
from ..jobs.manager import JobManager


def finite_number(value: Any) -> Any:
    try:
        finite = math.isfinite(float(value))
    except (TypeError, ValueError):
        return value
    if not finite:
        # NaN/Infinity cannot be reflected in a JSON validation error response.
        raise APIError(422, "数值必须是有限数值", code="finite_number_required")
    return value


FiniteNumber = Annotated[float, BeforeValidator(finite_number)]


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_jobs(request: Request) -> JobManager:
    jobs: JobManager = request.app.state.jobs
    return jobs
