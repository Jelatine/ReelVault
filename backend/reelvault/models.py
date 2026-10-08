from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Table,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class UTCDateTime(TypeDecorator[datetime]):
    """SQLite drops tzinfo; store naive UTC and hand back aware datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    password_changed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class TwoFactor(Base):
    """Encrypted authenticator enrollment; recovery codes are stored as hashes."""

    __tablename__ = "two_factor"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    secret_ciphertext: Mapped[str] = mapped_column(Text)
    enabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    pending_session_id: Mapped[str | None] = mapped_column(String(32))
    pending_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_counter: Mapped[int] = mapped_column(BigInteger, default=-1)
    recovery_hashes: Mapped[list[str]] = mapped_column(JSON, default=list)


class AuthSession(Base):
    """One row per logged-in device."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # Previous token stays valid for a short grace period after rotation.
    prev_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    device_name: Mapped[str] = mapped_column(String(128), default="")
    user_agent: Mapped[str] = mapped_column(String(512), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    remember: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    rotated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    event: Mapped[str] = mapped_column(String(32), index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    peer: Mapped[str] = mapped_column(String(64), default="")
    target: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class JobMetric(Base):
    """Bounded cumulative totals survive deletion of individual job records."""

    __tablename__ = "job_metrics"
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), primary_key=True)
    completed: Mapped[int] = mapped_column(BigInteger, default=0)
    duration_count: Mapped[int] = mapped_column(BigInteger, default=0)
    duration_sum: Mapped[float] = mapped_column(Float, default=0)
    buckets: Mapped[list[int]] = mapped_column(JSON, default=list)


class Folder(Base):
    __tablename__ = "folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("folders.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


video_tags = Table(
    "video_tags",
    Base.metadata,
    Column("video_id", ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class TagGroup(Base):
    __tablename__ = "tag_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("tag_groups.id", ondelete="SET NULL"), nullable=True
    )
    group: Mapped[TagGroup | None] = relationship(lazy="joined")


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    rating: Mapped[int] = mapped_column(Integer, default=0)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    captured_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    original_name: Mapped[str] = mapped_column(String(255), default="")
    # Paths are relative to the data directory.
    file_path: Mapped[str] = mapped_column(String(512))
    playable_path: Mapped[str | None] = mapped_column(String(512))
    source_path: Mapped[str | None] = mapped_column(String(1024), index=True)
    # Keep identifiers after source deletion so provenance is not silently lost.
    source_video_id: Mapped[str | None] = mapped_column(String(32), index=True)
    edit_params: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    edit_sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("folders.id", ondelete="SET NULL"), index=True
    )

    status: Mapped[str] = mapped_column(String(16), default="processing")  # processing|ready|error
    error: Mapped[str | None] = mapped_column(Text)

    size: Mapped[int] = mapped_column(Integer, default=0)
    duration: Mapped[float] = mapped_column(Float, default=0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    fps: Mapped[float] = mapped_column(Float, default=0)
    bitrate: Mapped[int] = mapped_column(Integer, default=0)
    container: Mapped[str] = mapped_column(String(64), default="")
    video_codec: Mapped[str] = mapped_column(String(32), default="")
    audio_codec: Mapped[str | None] = mapped_column(String(32))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metadata_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    custom_fields: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)

    has_poster: Mapped[bool] = mapped_column(Boolean, default=False)
    has_preview: Mapped[bool] = mapped_column(Boolean, default=False)
    has_sprite: Mapped[bool] = mapped_column(Boolean, default=False)
    asset_version: Mapped[int] = mapped_column(Integer, default=1)
    cover_time: Mapped[float | None] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)

    tags: Mapped[list[Tag]] = relationship(secondary=video_tags, lazy="selectin")


class SmartFolder(Base):
    __tablename__ = "smart_folders"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    filters: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class RecentSearch(Base):
    __tablename__ = "recent_searches"
    __table_args__ = (UniqueConstraint("user_id", "query"), {"sqlite_autoincrement": True})

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    query: Mapped[str] = mapped_column(String(512))
    used_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CollectionItem(Base):
    __tablename__ = "collection_items"

    collection_id: Mapped[int] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True
    )
    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer)


class EditPreset(Base):
    __tablename__ = "edit_presets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    edit: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RuntimeSetting(Base):
    __tablename__ = "runtime_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    duration: Mapped[float] = mapped_column(Float, default=0)
    stream_index: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SubtitleTrack(Base):
    __tablename__ = "subtitle_tracks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id", ondelete="RESTRICT"))
    label: Mapped[str] = mapped_column(String(128))
    language: Mapped[str] = mapped_column(String(35), default="und")
    generated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    source_signature: Mapped[list[Any] | None] = mapped_column(JSON)


class SubtitleCue(Base):
    __tablename__ = "subtitle_cues"
    __table_args__ = (CheckConstraint("start >= 0 AND end > start", name="subtitle_cue_time"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    track_id: Mapped[str] = mapped_column(
        ForeignKey("subtitle_tracks.id", ondelete="CASCADE"), index=True
    )
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)


class Playback(Base):
    __tablename__ = "playback"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[float] = mapped_column(Float, default=0)
    play_count: Mapped[int] = mapped_column(Integer, default=0)
    last_played_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ImportSource(Base):
    __tablename__ = "import_sources"

    path: Mapped[str] = mapped_column(Text, primary_key=True)
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Upload(Base):
    __tablename__ = "uploads"

    storage_id: Mapped[str] = mapped_column(String(32), default="local")

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(Integer)
    received: Mapped[int] = mapped_column(Integer, default=0)
    folder_id: Mapped[int | None] = mapped_column(Integer)
    relative_path: Mapped[str | None] = mapped_column(String(2048))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    # queued|running|paused|succeeded|failed|canceled
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=1)
    eta_seconds: Mapped[float | None] = mapped_column(Float)
    retry_of: Mapped[str | None] = mapped_column(String(32))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    video_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    result_video_id: Mapped[str | None] = mapped_column(String(32))
    result_file: Mapped[str | None] = mapped_column(String(512))
    progress: Mapped[float] = mapped_column(Float, default=0)
    message: Mapped[str] = mapped_column(String(255), default="")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    metrics_recorded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class SceneAnalysis(Base):
    __tablename__ = "scene_analyses"
    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    asset_version: Mapped[int] = mapped_column(Integer)
    signature: Mapped[list[Any]] = mapped_column(JSON)
    threshold: Mapped[float] = mapped_column(Float)
    min_interval: Mapped[float] = mapped_column(Float)
    duration: Mapped[float] = mapped_column(Float)
    cuts: Mapped[list[dict[str, float]]] = mapped_column(JSON)
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class VideoFingerprint(Base):
    __tablename__ = "video_fingerprints"
    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    asset_version: Mapped[int] = mapped_column(Integer)
    signature: Mapped[list[Any]] = mapped_column(JSON)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    visual: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    visual_error: Mapped[str | None] = mapped_column(Text)
    algorithm: Mapped[int] = mapped_column(Integer)
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class VideoVectorIndex(Base):
    __tablename__ = "video_vector_indexes"
    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    signature: Mapped[list[Any]] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(80))
    generation: Mapped[str] = mapped_column(String(32))
    interval: Mapped[float] = mapped_column(Float)
    frames: Mapped[int] = mapped_column(Integer)
    indexed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class VectorFrame(Base):
    __tablename__ = "vector_frames"
    __table_args__ = (
        UniqueConstraint("video_id", "ordinal"),
        CheckConstraint("timestamp >= 0 AND ordinal >= 0 AND ordinal < 1000"),
        CheckConstraint("length(embedding) = 2048"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[str] = mapped_column(
        ForeignKey("video_vector_indexes.video_id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[float] = mapped_column(Float)
    embedding: Mapped[bytes] = mapped_column(LargeBinary)


class AiAnalysis(Base):
    __tablename__ = "ai_analyses"
    video_id: Mapped[str] = mapped_column(
        ForeignKey("video_vector_indexes.video_id", ondelete="CASCADE"), primary_key=True
    )
    generation: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(80))
    face_model: Mapped[str | None] = mapped_column(String(80))
    suggestions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    analyzed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class FaceGroup(Base):
    __tablename__ = "face_groups"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class FaceObservation(Base):
    __tablename__ = "face_observations"
    __table_args__ = (
        UniqueConstraint("frame_id", "ordinal"),
        CheckConstraint("ordinal >= 0 AND ordinal < 16"),
        CheckConstraint("length(embedding) = 512"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(
        ForeignKey("ai_analyses.video_id", ondelete="CASCADE"), index=True
    )
    frame_id: Mapped[int] = mapped_column(
        ForeignKey("vector_frames.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    box: Mapped[list[float]] = mapped_column(JSON)
    score: Mapped[float] = mapped_column(Float)
    embedding: Mapped[bytes] = mapped_column(LargeBinary)
    group_id: Mapped[str | None] = mapped_column(
        ForeignKey("face_groups.id", ondelete="SET NULL"), index=True
    )
    manual: Mapped[bool] = mapped_column(Boolean, default=False)
    ignored: Mapped[bool] = mapped_column(Boolean, default=False)


class DuplicateMatch(Base):
    __tablename__ = "duplicate_matches"
    # Content identifiers survive source deletion and avoid quadratic identical-file pairs.
    left_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    right_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    score: Mapped[float] = mapped_column(Float)
    algorithm: Mapped[int] = mapped_column(Integer)


class Bookmark(Base):
    __tablename__ = "bookmarks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    position: Mapped[float] = mapped_column(Float)
    title: Mapped[str] = mapped_column(String(128))
    note: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))
    signature: Mapped[list[Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class HlsPackage(Base):
    __tablename__ = "hls_packages"
    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    generation: Mapped[str] = mapped_column(String(32))
    signature: Mapped[list[Any]] = mapped_column(JSON)
    renditions: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    size: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ShareLink(Base):
    __tablename__ = "share_links"
    __table_args__ = (
        CheckConstraint("(video_id IS NULL) != (collection_id IS NULL)", name="share_one_target"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True)
    video_id: Mapped[str | None] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    collection_id: Mapped[int | None] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE")
    )
    password_hash: Mapped[str | None] = mapped_column(String(255))
    allow_download: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ShareGrant(Base):
    __tablename__ = "share_grants"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    share_id: Mapped[str] = mapped_column(
        ForeignKey("share_links.id", ondelete="CASCADE"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
