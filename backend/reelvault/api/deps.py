from __future__ import annotations

from fastapi import Request

from ..config import Settings
from ..jobs.manager import JobManager


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_jobs(request: Request) -> JobManager:
    jobs: JobManager = request.app.state.jobs
    return jobs
