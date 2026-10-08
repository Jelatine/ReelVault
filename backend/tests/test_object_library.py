from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from reelvault import object_library
from reelvault.config import S3Config, Settings
from reelvault.db import make_engine, make_sessionmaker
from reelvault.library import abs_path, rel_path
from reelvault.locations import LocationUnavailable, location_of
from reelvault.media.duplicates import file_signature
from reelvault.media.scenes import source_signature
from reelvault.migrate import upgrade
from reelvault.models import ObjectNamespace, OriginalObject
from reelvault.object_store import ObjectStore, ObjectStoreError
from reelvault.object_types import OriginalPath

from .test_object_store import KEY, ProtocolClient, S3Failure


class NamespacePeer(ProtocolClient):
    def get_object(self, *, Bucket, Key, IfMatch=None, VersionId=None, Range=None):
        if Key.endswith("/.reelvault-namespace.json"):
            if Key not in self.objects:
                raise S3Failure("NoSuchKey")
            data = self.objects[Key]["data"]
            body = io.BytesIO(data)
            self.bodies.append(body)
            return {"Body": body, "ContentLength": len(data)}
        return super().get_object(
            Bucket=Bucket, Key=Key, IfMatch=IfMatch, VersionId=VersionId, Range=Range
        )

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch, ContentType, ContentMD5):
        assert IfNoneMatch == "*"
        assert (
            ContentMD5
            == base64.b64encode(hashlib.md5(Body, usedforsecurity=False).digest()).decode()
        )
        if Key in self.objects:
            raise S3Failure("PreconditionFailed")
        self.objects[Key] = {
            "data": Body,
            "ContentLength": len(Body),
            "LastModified": datetime.now(UTC),
        }


@pytest.fixture
def registry(settings, monkeypatch):
    settings.s3 = S3Config(
        bucket="test-originals", access_key="private-key", secret_key="private-secret"
    )
    settings.ensure_dirs()
    engine = make_engine(settings.db_path)
    upgrade(engine)
    sessions = make_sessionmaker(engine)
    peer = NamespacePeer()
    monkeypatch.setattr(
        object_library, "ObjectStore", lambda config: ObjectStore(config, client=peer)
    )
    asyncio.run(object_library.initialize(settings, sessions))
    assert settings.s3_current and not settings.s3_error
    try:
        yield settings, sessions, peer
    finally:
        engine.dispose()


def publish(registry, tmp_path):
    settings, sessions, peer = registry
    source = tmp_path / "original.mp4"
    source.write_bytes(b"original video bytes")
    with sessions() as db:
        path = object_library.reserve_original(db, settings, KEY)
        db.commit()
        token = db.get(OriginalObject, path).transfer_id
    config = object_library.bound_config(settings, settings.s3_current)
    ref = ObjectStore(config, client=peer).put(source, KEY, transfer_id=token)
    with sessions() as db:
        object_library.adopt_original(db, settings, path, ref)
    return path, ref


def test_restart_keeps_credential_free_namespace_and_pinned_reference(
    registry, tmp_path, monkeypatch
):
    settings, sessions, peer = registry
    path, ref = publish(registry, tmp_path)
    with sessions() as db:
        namespace = db.scalar(select(ObjectNamespace))
        assert "private-key" not in json.dumps(namespace.descriptor)
        assert "private-secret" not in json.dumps(namespace.descriptor)
        assert db.get(OriginalObject, path).state == "ready"

    def offline(config):
        raise AssertionError("An existing namespace must load without contacting offline S3")

    monkeypatch.setattr(object_library, "ObjectStore", offline)
    restarted = Settings(data_dir=settings.data_dir, s3=settings.s3)
    asyncio.run(object_library.initialize(restarted, sessions))
    assert restarted.s3_current == settings.s3_current and restarted.s3_objects[path] == ref
    assert isinstance(abs_path(restarted, path), OriginalPath)
    # A removed deployment credential still permits its verified local cache,
    # but remote access cannot silently switch to another configured backend.
    unconfigured = Settings(data_dir=settings.data_dir)
    asyncio.run(object_library.initialize(unconfigured, sessions))
    assert unconfigured.s3_objects[path] == ref and unconfigured.s3_current is None
    with pytest.raises(ObjectStoreError, match="not configured"):
        object_library.bound_config(unconfigured, settings.s3_current)


def test_cache_recreation_keeps_source_and_duplicate_identities(registry, tmp_path):
    settings, _, _ = registry
    path, ref = publish(registry, tmp_path)
    local = abs_path(settings, path)
    local.parent.mkdir(parents=True)
    local.write_bytes(b"original video bytes")
    assert rel_path(settings, local) == path and location_of(path) == "s3"
    signature, fingerprint = source_signature(local), file_signature(local)
    local.unlink()
    assert source_signature(abs_path(settings, path)) == signature
    assert file_signature(abs_path(settings, path)) == fingerprint
    local.write_bytes(b"original video bytes")
    assert source_signature(abs_path(settings, path)) == signature
    assert file_signature(abs_path(settings, path)) == fingerprint
    assert fingerprint[3] == ref.size


def test_existing_reference_cannot_be_replaced(registry, tmp_path):
    settings, sessions, _ = registry
    path, ref = publish(registry, tmp_path)
    with sessions() as db:
        with pytest.raises(ObjectStoreError, match="already published"):
            object_library.reserve_original(db, settings, KEY)
        with pytest.raises(ObjectStoreError, match="cannot be replaced"):
            object_library.adopt_original(
                db, settings, path, ref.model_copy(update={"etag": "replacement"})
            )
        db.rollback()
    assert settings.s3_objects[path] == ref


def test_other_library_cannot_claim_marker(registry, tmp_path):
    settings, _, peer = registry
    other = Settings(data_dir=tmp_path / "other", s3=settings.s3)
    other.ensure_dirs()
    engine = make_engine(other.db_path)
    upgrade(engine)
    sessions = make_sessionmaker(engine)
    try:
        marker = dict(peer.objects)
        asyncio.run(object_library.initialize(other, sessions))
        assert other.s3_current is None and other.s3_error == "对象存储前缀已属于其他视频库"
        assert peer.objects == marker
    finally:
        engine.dispose()


def test_namespace_replacement_refuses_all_remote_operations(registry, tmp_path):
    settings, _, peer = registry
    path, ref = publish(registry, tmp_path)
    config = object_library.bound_config(settings, settings.s3_current)
    marker = settings.s3.prefix + "/.reelvault-namespace.json"
    replacement = {"version": 1, "id": uuid4().hex, "library_id": settings.s3_library_id}
    peer.objects[marker]["data"] = json.dumps(replacement).encode()
    with pytest.raises(ObjectStoreError, match="identity mismatch"):
        ObjectStore(config, client=peer)
    assert peer.objects[settings.s3.prefix + "/" + ref.key]["data"] == b"original video bytes"


@pytest.mark.parametrize("suffix", ["../other.mp4", "/absolute", "a.mp4", KEY + "/extra"])
def test_invalid_object_paths_never_resolve_to_cache(registry, suffix):
    settings, _, _ = registry
    with pytest.raises(ValueError):
        abs_path(settings, "s3/" + settings.s3_current + "/" + suffix)


def test_unknown_namespace_and_symlink_cache_are_refused(registry, tmp_path):
    settings, _, _ = registry
    with pytest.raises(LocationUnavailable):
        abs_path(settings, f"s3/{uuid4().hex}/{KEY}")
    outside = tmp_path / "outside"
    outside.mkdir()
    (settings.data_dir / "objects").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        abs_path(settings, f"s3/{settings.s3_current}/{KEY}")


@pytest.mark.parametrize(
    "data",
    [
        b"{}",
        b"invalid json",
        b"a" * 1025,
        json.dumps({"version": True, "id": "a" * 32, "library_id": "b" * 32}).encode(),
        json.dumps({"version": 1, "id": "../evil", "library_id": "b" * 32}).encode(),
    ],
)
def test_malformed_marker_is_never_adopted(data):
    peer = NamespacePeer()
    config = S3Config(bucket="test-originals")
    peer.objects[config.prefix + "/.reelvault-namespace.json"] = {"data": data}
    with (
        ObjectStore(config, client=peer) as store,
        pytest.raises(ObjectStoreError, match="identity is invalid"),
    ):
        store.bind_namespace("b" * 32)
    assert all(body.closed for body in peer.bodies)


def _video(path, **fields):
    from reelvault.models import Video

    return Video(title="clip", original_name="clip.mp4", file_path=path, size=20, **fields)


def test_purge_deletes_remote_object_before_records(registry, tmp_path):
    settings, sessions, peer = registry
    path, ref = publish(registry, tmp_path)
    local = abs_path(settings, path)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(b"original video bytes")
    object_library.mark_cache(local, ref)
    remote = settings.s3.prefix + "/" + ref.key
    with sessions() as db:
        video = _video(path)
        db.add(video)
        db.commit()
        object_library.release_remote(db, settings, [video])
        assert remote not in peer.objects
        db.delete(video)
        object_library.forget_original(db, settings, path)
        db.commit()
        assert db.get(OriginalObject, path) is None
    assert path not in settings.s3_objects
    assert not local.exists() and not (local.parent / (local.name + ".verified.json")).exists()


def test_shared_original_is_kept_until_last_reference(registry, tmp_path):
    settings, sessions, peer = registry
    path, ref = publish(registry, tmp_path)
    with sessions() as db:
        first, second = _video(path), _video(path)
        db.add_all([first, second])
        db.commit()
        object_library.release_remote(db, settings, [first])
        db.delete(first)
        object_library.forget_original(db, settings, path)
        db.commit()
        assert db.get(OriginalObject, path) is not None
    assert settings.s3.prefix + "/" + ref.key in peer.objects


@pytest.mark.parametrize("failure", ["offline", "replaced", "unconfigured"])
def test_failed_remote_release_keeps_every_record(registry, tmp_path, monkeypatch, failure):
    settings, sessions, peer = registry
    path, ref = publish(registry, tmp_path)
    remote = settings.s3.prefix + "/" + ref.key
    if failure == "offline":
        monkeypatch.setattr(
            peer, "delete_object", lambda **_: (_ for _ in ()).throw(S3Failure("Unavailable"))
        )
    elif failure == "replaced":
        peer.objects[remote]["ETag"] = '"external-replacement"'
    else:
        settings.s3 = None
    with sessions() as db:
        video = _video(path)
        db.add(video)
        db.commit()
        with pytest.raises(ObjectStoreError):
            object_library.release_remote(db, settings, [video])
        assert db.get(OriginalObject, path).state == "ready"
    assert remote in peer.objects


def test_pending_original_only_releases_its_own_transfer(registry, tmp_path):
    settings, sessions, peer = registry
    source = tmp_path / "original.mp4"
    source.write_bytes(b"original video bytes")
    with sessions() as db:
        path = object_library.reserve_original(db, settings, KEY)
        video = _video(path)
        db.add(video)
        db.commit()
        token = db.get(OriginalObject, path).transfer_id
    config = object_library.bound_config(settings, settings.s3_current)
    remote = settings.s3.prefix + "/" + KEY
    # Published, then crashed before adoption: the transfer token proves ownership.
    ObjectStore(config, client=peer).put(source, KEY, transfer_id=token)
    with sessions() as db:
        object_library.release_remote(db, settings, [db.get(type(video), video.id)])
    assert remote not in peer.objects
    ObjectStore(config, client=peer).put(source, KEY, transfer_id=uuid4().hex)
    with sessions() as db:
        object_library.release_remote(db, settings, [db.get(type(video), video.id)])
    assert remote in peer.objects


def _cached(settings, path, ref, pinned=False):
    local = abs_path(settings, path)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(b"original video bytes")
    object_library.mark_cache(local, ref, pinned=pinned)
    return local


def test_release_after_job_respects_pins_busy_jobs_and_keep_local(registry, tmp_path):
    settings, _, _ = registry
    path, ref = publish(registry, tmp_path)
    local = _cached(settings, path, ref)
    object_library.release_after_job(settings, [path], busy={path})
    assert object_library.cache_valid(local, ref)
    settings.s3 = settings.s3.model_copy(update={"keep_local": True})
    object_library.release_after_job(settings, [path], busy=set())
    assert object_library.cache_valid(local, ref)
    settings.s3 = settings.s3.model_copy(update={"keep_local": False})
    local.unlink()
    local = _cached(settings, path, ref, pinned=True)
    object_library.release_after_job(settings, [path], busy=set())
    assert object_library.cache_valid(local, ref) and object_library.cache_pinned(local)
    local.unlink()
    local = _cached(settings, path, ref)
    object_library.release_after_job(settings, [path], busy=set())
    assert not local.exists()


def test_unarchived_original_is_never_released(registry):
    settings, sessions, _ = registry
    with sessions() as db:
        path = object_library.reserve_original(db, settings, KEY)
        db.commit()
    local = abs_path(settings, path)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(b"only copy")
    object_library.release_after_job(settings, [path], busy=set())
    assert not object_library.release_cache(settings, path)
    assert local.read_bytes() == b"only copy"


def test_backup_restore_keeps_s3_originals_without_local_copies(registry, tmp_path):
    from reelvault.backup import BackupError, create_backup, restore_backup
    from reelvault.models import RuntimeSetting, Video

    settings, sessions, peer = registry
    path, ref = publish(registry, tmp_path)
    with sessions() as db:
        db.add(_video(path, status="ready"))
        db.add(
            RuntimeSetting(
                key="storage_locations", value={"storage_locations": {}, "storage_default": "s3"}
            )
        )
        db.commit()
    archive = create_backup(settings, tmp_path / "s3.zip")
    config = json.loads(zipfile.ZipFile(archive).read("config.json"))
    assert "s3" not in config and "private-secret" not in json.dumps(config)

    target = Settings(data_dir=tmp_path / "target", update_check=False)
    target.ensure_dirs()
    restore_backup(archive, target)  # No local copy is required for archived originals.
    restored = Settings(data_dir=target.data_dir, s3=settings.s3)
    engine = make_engine(restored.db_path)
    try:
        asyncio.run(object_library.initialize(restored, make_sessionmaker(engine)))
    finally:
        engine.dispose()
    assert restored.s3_current == settings.s3_current
    assert restored.s3_objects[path] == ref

    with sessions() as db:
        db.query(Video).delete()
        db.add(_video(f"s3/{settings.s3_current}/{'b' * 32}.mp4", status="ready"))
        db.commit()
    broken = create_backup(settings, tmp_path / "broken.zip")
    with pytest.raises(BackupError, match="对象存储原视频记录"):
        restore_backup(broken, target, replace=True)


def test_startup_recovers_interrupted_upload_before_jobs(registry, tmp_path, monkeypatch):
    from reelvault.media import object_transfer

    settings, sessions, peer = registry
    with sessions() as db:
        path = object_library.reserve_original(db, settings, KEY)
        db.commit()
        token = db.get(OriginalObject, path).transfer_id
    config = object_library.bound_config(settings, settings.s3_current)
    source = tmp_path / "staged.mp4"
    source.write_bytes(b"staged original")
    # Crashed after publishing, before adoption: a journal and an owned object remain.
    directory = settings.tmp_dir / ("s3-transfer-" + token)
    directory.mkdir(mode=0o700)
    object_transfer._request(
        config, {"operation": "put", "key": KEY, "transfer_id": token}, directory
    )
    ObjectStore(config, client=peer).put(source, KEY, transfer_id=token)
    monkeypatch.setattr(object_transfer, "ObjectStore", lambda c: ObjectStore(c, client=peer))
    restarted = Settings(data_dir=settings.data_dir, s3=settings.s3)
    asyncio.run(object_library.initialize(restarted, sessions))
    assert settings.s3.prefix + "/" + KEY not in peer.objects and not directory.exists()
    with sessions() as db:
        assert db.get(OriginalObject, path).state == "pending"


def test_missing_s3_dependency_is_reported_not_fatal(settings, monkeypatch):
    def unavailable(config):
        raise ObjectStoreError("S3 support requires the optional s3 dependency")

    settings.s3 = S3Config(bucket="test-originals")
    settings.ensure_dirs()
    engine = make_engine(settings.db_path)
    upgrade(engine)
    monkeypatch.setattr(object_library, "ObjectStore", unavailable)
    try:
        asyncio.run(object_library.initialize(settings, make_sessionmaker(engine)))
    finally:
        engine.dispose()
    assert settings.s3_current is None
    assert settings.s3_error == "未安装对象存储组件（s3 可选依赖）"
