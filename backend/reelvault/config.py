from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

PACKAGE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Runtime configuration, read from REELVAULT_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="REELVAULT_", env_file=".env", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            JsonConfigSettingsSource(
                settings_cls, json_file=os.environ.get("REELVAULT_CONFIG_FILE")
            ),
            file_secret_settings,
        )

    data_dir: Path = Path("./data")
    host: str = "0.0.0.0"
    port: int = 8080

    # Initial admin account. When unset, the first visitor is asked to create one.
    admin_user: str | None = None
    admin_password: str | None = None

    # Number of concurrent ffmpeg jobs.
    workers: int = 2
    encoder: Literal["software", "auto", "videotoolbox", "qsv", "vaapi", "nvenc"] = "software"
    hls_enabled: bool = False
    hls_min_size_mb: int = Field(256, ge=0, le=102400)
    hls_max_cache_gb: int = Field(20, ge=1, le=1024)
    storage_warning_mb: int = Field(1024, ge=0, le=1048576)
    storage_warning_percent: int = Field(5, ge=0, le=100)
    vaapi_device: str = "/dev/dri/renderD128"

    # Session lifetime without "remember me" (sliding, in hours).
    session_idle_hours: int = 12
    # Session lifetime with "remember me" (sliding, in days).
    remember_days: int = 30
    # Rotate remembered tokens after this many days.
    token_rotate_days: int = 7
    # Mark cookies Secure (enable when served over HTTPS).
    secure_cookies: bool = False

    # Login rate limiting per client IP.
    login_max_failures: int = 5
    login_lock_minutes: int = 5

    # Directory holding the built frontend; auto-detected when unset.
    static_dir: Path | None = None
    # Optional directory that can be scanned to import existing videos.
    import_dir: Path | None = None

    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"

    upload_chunk_size: int = 8 * 1024 * 1024
    audio_upload_max_mb: int = Field(128, ge=1, le=2048)
    # Zero disables automatic permanent deletion.
    trash_retention_days: int = Field(30, ge=0)

    # Update checks against GitHub Releases.
    update_repo: str = "Jelatine/ReelVault"
    # GitHub API base URL (change for GitHub Enterprise or a mirror).
    update_api_url: str = "https://api.github.com"
    update_check: bool = True
    update_check_interval_hours: int = 12
    update_include_prereleases: bool = False
    # Optional token to raise the GitHub API rate limit.
    github_token: str | None = None
    # auto | package | docker | source | none
    install_mode: str = "auto"
    # Allow one-click upgrades from the web UI (release-package installs only).
    allow_self_update: bool = True
    # Enabled by the Ubuntu installer after installing its root-owned path helper.
    systemd_sync: bool | None = None
    uv: str = "uv"

    @property
    def library_dir(self) -> Path:
        return self.data_dir / "library"

    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def assets_dir(self) -> Path:
        return self.data_dir / "assets"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "reelvault.db"

    def ensure_dirs(self) -> None:
        for d in (
            self.library_dir,
            self.derived_dir,
            self.tmp_dir,
            self.exports_dir,
            self.assets_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def resolve_static_dir(self) -> Path | None:
        candidates = [self.static_dir] if self.static_dir else []
        candidates += [PACKAGE_DIR / "static", PACKAGE_DIR.parent.parent / "frontend" / "dist"]
        for c in candidates:
            if c and (c / "index.html").is_file():
                return c
        return None
