"""End-to-end S3 originals against an owned MinIO server (skipped without one)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from reelvault.config import S3Config, Settings
from reelvault.library import abs_path
from reelvault.main import create_app
from reelvault.models import OriginalObject, Video
from reelvault.object_library import cache_valid
from reelvault.object_store import ObjectStore, ObjectStoreError

from .conftest import HEADERS, login, upload_ready, wait_ready


@pytest.fixture
def minio(settings: Settings) -> Iterator[S3Config]:
    endpoint = os.environ.get("REELVAULT_TEST_S3_ENDPOINT")
    if not endpoint:
        pytest.skip("Requires an owned MinIO or S3-compatible test server")
    config = S3Config(
        bucket="reelvault-test-" + uuid4().hex,
        endpoint=endpoint,
        access_key=os.environ["REELVAULT_TEST_S3_ACCESS_KEY"],
        secret_key=os.environ["REELVAULT_TEST_S3_SECRET_KEY"],
        part_size_mb=5,
    )
    with ObjectStore(config) as store:
        store.client.create_bucket(Bucket=config.bucket)
        store.client.put_bucket_versioning(
            Bucket=config.bucket, VersioningConfiguration={"Status": "Enabled"}
        )
        try:
            yield config
        finally:
            versions = store.client.list_object_versions(Bucket=config.bucket)
            for item in versions.get("Versions", []) + versions.get("DeleteMarkers", []):
                store.client.delete_object(
                    Bucket=config.bucket, Key=item["Key"], VersionId=item["VersionId"]
                )
            store.client.delete_bucket(Bucket=config.bucket)


def test_upload_archive_proxy_redownload_and_purge(settings, minio, samples):
    settings.s3 = minio
    data = samples["a"].read_bytes()
    with TestClient(create_app(settings), headers=HEADERS) as client:
        login(client)
        assert settings.s3_current and not settings.s3_error
        response = client.put("/api/system/locations/default", json={"location_id": "s3"})
        assert response.status_code == 200, response.text
        video = upload_ready(client, samples["a"])
        assert video["storage_id"] == "s3"
        with client.app.state.sessionmaker() as db:
            path = db.get(Video, video["id"]).file_path
            assert db.get(OriginalObject, path).state == "ready"
        ref = settings.s3_objects[path]
        assert ref.size == len(data)
        local = abs_path(settings, path)
        assert cache_valid(local, ref)
        with ObjectStore(minio) as store:
            assert store.head(ref.key).sha256 == ref.sha256

        # Without a local copy, playback is a pinned-version proxy.
        local.unlink()
        assert not cache_valid(local, ref)
        part = client.get(f"/api/videos/{video['id']}/stream", headers={"Range": "bytes=100-299"})
        assert part.status_code == 206 and part.content == data[100:300]
        assert minio.endpoint not in str(part.headers) and minio.bucket not in str(part.headers)

        # Jobs that need bytes download and verify a fresh cache.
        assert client.post(f"/api/videos/{video['id']}/reprocess").status_code == 200
        wait_ready(client, video["id"])
        assert cache_valid(local, ref) and local.read_bytes() == data

        assert client.delete(f"/api/videos/{video['id']}?permanent=true").status_code == 200
        with ObjectStore(minio) as store, pytest.raises(ObjectStoreError):
            store.head(ref.key)
        assert not local.exists() and path not in settings.s3_objects


def test_edit_outputs_and_replacement_backups_are_archived(settings, minio, samples):
    from .test_edit import result_video, run_edit

    settings.s3 = minio
    with TestClient(create_app(settings), headers=HEADERS) as client:
        login(client)
        source = upload_ready(client, samples["a"])
        assert source["storage_id"] == "local"
        trim = {"op": "trim", "segments": [{"start": 0, "end": 2}]}
        created = result_video(
            client, run_edit(client, source["id"], trim, {"mode": "new", "storage_id": "s3"})
        )
        assert created["storage_id"] == "s3"
        with client.app.state.sessionmaker() as db:
            before = db.get(Video, created["id"]).file_path
        old_ref = settings.s3_objects[before]

        job = run_edit(
            client,
            created["id"],
            {**trim, "segments": [{"start": 0, "end": 1}]},
            {"mode": "replace"},
        )
        replaced = wait_ready(client, created["id"])
        assert replaced["storage_id"] == "s3" and replaced["duration"] < 1.6
        with client.app.state.sessionmaker() as db:
            current = db.get(Video, created["id"])
            backup = db.get(Video, current.source_video_id)
            assert current.file_path != before and backup.file_path == before
            assert backup.deleted_at is not None
            assert db.get(OriginalObject, current.file_path).state == "ready"
        wait_ready(client, backup.id)
        assert job["status"] == "succeeded"
        new_ref = settings.s3_objects[current.file_path]
        with ObjectStore(minio) as store:
            assert store.head(old_ref.key).sha256 == old_ref.sha256
            assert store.head(new_ref.key).sha256 == new_ref.sha256

        # Purging the pre-edit backup removes only its own object.
        assert client.delete(f"/api/videos/{backup.id}?permanent=true").status_code == 200
        with ObjectStore(minio) as store:
            with pytest.raises(ObjectStoreError):
                store.head(old_ref.key)
            assert store.head(new_ref.key).sha256 == new_ref.sha256
