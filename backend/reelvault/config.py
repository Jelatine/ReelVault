from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from .object_types import ObjectRef

PACKAGE_DIR = Path(__file__).resolve().parent


class S3Config(BaseModel):
    """Deployment-only credentials for one private original-video namespace."""

    bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    prefix: str = Field(default="reelvault", min_length=1, max_length=512)
    endpoint: str | None = None
    region: str = Field(default="us-east-1", pattern=r"^[a-zA-Z0-9-]{1,64}$")
    access_key: SecretStr | None = None
    secret_key: SecretStr | None = None
    session_token: SecretStr | None = None
    addressing_style: Literal["auto", "path", "virtual"] = "path"
    part_size_mb: int = Field(default=8, ge=5, le=128)
    namespace_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    library_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")

    @field_validator("prefix")
    @classmethod
    def namespace(cls, value: str) -> str:
        if not all(
            re.fullmatch(r"[a-zA-Z0-9_.-]+", part) and part not in {".", ".."}
            for part in value.split("/")
        ):
            raise ValueError("S3 prefix requires nonempty safe path segments")
        return value

    @field_validator("endpoint")
    @classmethod
    def service_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            len(value) > 2048
            or any(not 33 <= ord(c) <= 126 for c in value)
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)
        ):
            raise ValueError("S3 endpoint requires an HTTP(S) origin without credentials")
        return value.rstrip("/")

    @model_validator(mode="after")
    def credential_pair(self) -> S3Config:
        if bool(self.namespace_id) != bool(self.library_id):
            raise ValueError("S3 namespace identity requires a library identity")
        if bool(self.access_key) != bool(self.secret_key):
            raise ValueError("S3 access key and secret key must be supplied together")
        if self.session_token and not self.access_key:
            raise ValueError("Explicit S3 session token requires explicit credentials")
        for value in (self.access_key, self.secret_key, self.session_token):
            if value is not None and not value.get_secret_value():
                raise ValueError("S3 credentials must not be empty")
        return self


class StorageRoot(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    path: Path

    @field_validator("path")
    @classmethod
    def absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Storage path must be absolute")
        return value


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
    storage_locations: dict[str, StorageRoot] = Field(default_factory=dict)
    storage_default: str = "local"
    s3: S3Config | None = Field(default=None, repr=False, exclude=True)
    s3_current: str | None = Field(default=None, exclude=True)
    s3_library_id: str | None = Field(default=None, exclude=True)
    s3_namespaces: dict[str, dict[str, Any]] = Field(default_factory=dict, exclude=True)
    s3_objects: dict[str, ObjectRef] = Field(default_factory=dict, exclude=True)
    s3_error: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def valid_storage_registry(self) -> Settings:
        if len(self.storage_locations) > 32 or any(
            not re.fullmatch(r"[a-f0-9]{32}", key) for key in self.storage_locations
        ):
            raise ValueError("Invalid storage location identifiers")
        if (
            self.storage_default not in {"local", "s3"}
            and self.storage_default not in self.storage_locations
        ):
            raise ValueError("Default storage location is not registered")
        return self

    host: str = "0.0.0.0"
    port: int = 8080

    # Initial admin account. When unset, the first visitor is asked to create one.
    admin_user: str | None = None
    admin_password: str | None = None

    # Number of concurrent ffmpeg jobs.
    workers: int = 2
    webdav_enabled: bool = Field(False, exclude=True)
    webdav_token_hash: str = Field("", repr=False, exclude=True, pattern=r"^([a-f0-9]{64})?$")
    transcription_enabled: bool = False
    transcription_model: Literal["tiny", "base", "small", "medium", "large-v3"] = "base"
    transcription_download_model: bool = False
    transcription_threads: int = Field(2, ge=1, le=32)
    transcription_max_hours: int = Field(6, ge=1, le=24)
    vision_enabled: bool = Field(False, exclude=True)
    vision_url: str = Field("http://127.0.0.1:8091", exclude=True)
    vision_token: str = Field("", repr=False, exclude=True)
    vision_max_frames: int = Field(240, ge=1, le=1000)
    ai_enabled: bool = Field(False, exclude=True)
    ai_faces_enabled: bool = Field(False, exclude=True)

    @field_validator("vision_url")
    @classmethod
    def valid_vision_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            len(value) > 2048
            or any(not 33 <= ord(c) <= 126 for c in value)
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)
        ):
            raise ValueError(
                "Vision URL must be an HTTP(S) service URL without credentials or query"
            )
        return value.rstrip("/")

    @field_validator("vision_token")
    @classmethod
    def valid_vision_token(cls, value: str) -> str:
        if value and (not 32 <= len(value) <= 256 or any(not 33 <= ord(c) <= 126 for c in value)):
            raise ValueError("Vision token requires 32–256 printable ASCII characters")
        return value

    @model_validator(mode="after")
    def vision_credentials(self) -> Settings:
        if self.vision_enabled and not self.vision_token:
            raise ValueError("Vision service requires a deployment token")
        return self

    encoder: Literal["software", "auto", "videotoolbox", "qsv", "vaapi", "nvenc"] = "software"
    hls_enabled: bool = False
    playable_eager_max_mb: int = Field(256, ge=0, le=102400)
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
    audit_retention_days: int = Field(90, ge=1, le=3650)
    audit_max_events: int = Field(10000, ge=100, le=1000000)
    metrics_token: str = Field("", repr=False, exclude=True)

    @field_validator("metrics_token")
    @classmethod
    def valid_metrics_token(cls, value: str) -> str:
        if value and (not 32 <= len(value) <= 256 or any(not 33 <= ord(c) <= 126 for c in value)):
            raise ValueError(
                "Metrics token requires 32-256 printable ASCII characters, without spaces"
            )
        return value

    # Directory holding the built frontend; auto-detected when unset.
    static_dir: Path | None = None
    # Optional directory that can be scanned to import existing videos.
    import_dir: Path | None = None
    link_import_enabled: bool = False
    link_import_max_mb: int = Field(1024, ge=16, le=102400)
    link_import_timeout_minutes: int = Field(30, ge=1, le=1440)
    # Deployment-only opt-in for trusted private video sources; never set via the web UI.
    link_import_allow_private: bool = False
    yt_dlp: str = "yt-dlp"

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
