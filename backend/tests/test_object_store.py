from __future__ import annotations

import base64
import hashlib
import io
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from reelvault.config import S3Config, Settings
from reelvault.object_store import ObjectRef, ObjectStore, ObjectStoreError

KEY = "a" * 32 + ".mp4"


class S3Failure(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}
        super().__init__("SDK secret URL must not escape")


class ProtocolClient:
    """Small deterministic S3 peer for failure injection; integration uses MinIO."""

    def __init__(self):
        self.objects = {}
        self.uploads = {}
        self.aborted = []
        self.closed = False
        self.fail_part = 0
        self.bodies = []

    def close(self):
        self.closed = True

    def create_multipart_upload(self, *, Bucket, Key, ContentType, Metadata):
        uid = uuid4().hex
        self.uploads[uid] = {"key": Key, "metadata": Metadata, "parts": {}}
        return {"UploadId": uid}

    def upload_part(self, *, Bucket, Key, UploadId, PartNumber, Body, ContentMD5):
        assert (
            ContentMD5
            == base64.b64encode(hashlib.md5(Body, usedforsecurity=False).digest()).decode()
        )
        if PartNumber == self.fail_part:
            raise S3Failure("ServiceUnavailable")
        self.uploads[UploadId]["parts"][PartNumber] = Body
        return {"ETag": f'"part-{PartNumber}"'}

    def complete_multipart_upload(self, *, Bucket, Key, UploadId, MultipartUpload, IfNoneMatch):
        assert IfNoneMatch == "*"
        if Key in self.objects:
            raise S3Failure("PreconditionFailed")
        upload = self.uploads.pop(UploadId)
        data = b"".join(upload["parts"][p["PartNumber"]] for p in MultipartUpload["Parts"])
        self.objects[Key] = {
            "data": data,
            "Metadata": upload["metadata"],
            "ContentLength": len(data),
            "ETag": '"multipart-etag-not-a-sha256"',
            "LastModified": datetime.now(UTC),
        }
        return {}

    def abort_multipart_upload(self, *, Bucket, Key, UploadId):
        self.aborted.append(UploadId)
        self.uploads.pop(UploadId, None)

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise S3Failure("404")
        return self.objects[Key]

    def get_object(self, *, Bucket, Key, IfMatch, VersionId=None, Range=None):
        obj = self.objects[Key]
        if obj["ETag"] != IfMatch:
            raise S3Failure("PreconditionFailed")
        data = obj["data"]
        if Range:
            start, end = Range[6:].split("-")
            data = data[int(start) : int(end) + 1 if end else None]
        body = io.BytesIO(data)
        self.bodies.append(body)
        return {"ContentLength": len(data), "ETag": obj["ETag"], "Body": body}

    def delete_object(self, *, Bucket, Key, IfMatch, VersionId=None):
        obj = self.objects.get(Key)
        if obj and obj["ETag"] != IfMatch:
            raise S3Failure("PreconditionFailed")
        self.objects.pop(Key, None)


@pytest.fixture
def store():
    peer = ProtocolClient()
    with ObjectStore(
        S3Config(bucket="test-bucket", prefix="library/private", part_size_mb=5), client=peer
    ) as s:
        yield s, peer
    assert peer.closed


@pytest.mark.parametrize(
    "override",
    [
        {"prefix": "../other"},
        {"prefix": "other//library"},
        {"prefix": "/root"},
        {"prefix": "root/"},
        {"prefix": "a\\b"},
        {"prefix": "."},
        {"endpoint": "https://user:secret@host"},
        {"endpoint": "https://host/api"},
        {"endpoint": "https://host?secret=token"},
        {"endpoint": "file:///tmp"},
        {"endpoint": "http://host:99999"},
        {"access_key": "key"},
        {"secret_key": "secret"},
        {"access_key": "", "secret_key": ""},
        {"session_token": "token"},
    ],
)
def test_invalid_deployment_config_is_rejected(override):
    with pytest.raises(ValidationError):
        S3Config(bucket="test-bucket", **override)


def test_credentials_are_not_exported_or_printed():
    config = S3Config(bucket="test-bucket", access_key="private-key", secret_key="private-secret")
    settings = Settings(s3=config)
    for output in (
        repr(settings),
        repr(config),
        config.model_dump_json(),
        settings.model_dump_json(),
    ):
        assert "private-key" not in output and "private-secret" not in output
    assert "s3" not in settings.model_dump()


def test_multipart_range_verified_download_and_delete(store, tmp_path):
    s, peer = store
    src = tmp_path / "source.mp4"
    data = os.urandom(11 * 1024**2)
    src.write_bytes(data)
    progress, started = [], []
    ref = s.put(src, KEY, on_progress=progress.append, on_started=started.append)
    assert ref.sha256 == hashlib.sha256(data).hexdigest()
    assert ref.size == len(data) and ref.etag != ref.sha256
    assert progress == sorted(progress) and progress[-1] == 1
    assert len(started) == 1 and not peer.uploads and not peer.aborted
    with s.open(ref, start=13, end=36) as body:
        assert body.read() == data[13:37]
    dest = tmp_path / "download.mp4"
    s.download(ref, dest)
    assert dest.read_bytes() == data and src.read_bytes() == data
    assert abs(dest.stat().st_mtime - ref.modified.timestamp()) < 0.001
    assert all(body.closed for body in peer.bodies)
    s.delete(ref)
    assert not peer.objects


def test_failed_part_aborts_and_never_publishes(store, tmp_path):
    s, peer = store
    src = tmp_path / "source.mp4"
    src.write_bytes(b"a" * (6 * 1024**2))
    peer.fail_part = 2
    with pytest.raises(ObjectStoreError, match="ServiceUnavailable") as error:
        s.put(src, KEY)
    assert "secret" not in str(error.value)
    assert peer.aborted and not peer.uploads and not peer.objects and src.exists()


def test_existing_original_is_never_overwritten(store, tmp_path):
    s, peer = store
    src = tmp_path / "source.mp4"
    src.write_bytes(b"old original")
    ref = s.put(src, KEY)
    src.write_bytes(b"new video")
    with pytest.raises(ObjectStoreError, match="PreconditionFailed"):
        s.put(src, KEY)
    assert s.head(KEY) == ref and peer.aborted and not peer.uploads


def test_source_changes_abort_before_publish(store, tmp_path):
    s, peer = store
    src = tmp_path / "source.mp4"
    src.write_bytes(b"original")

    def changed(upload_id):
        src.write_bytes(b"replacement")

    with pytest.raises(ObjectStoreError, match="Source changed"):
        s.put(src, KEY, on_started=changed)
    assert not peer.objects and not peer.uploads and peer.aborted


def test_corruption_preserves_destination_and_cleans_partial(store, tmp_path):
    s, peer = store
    src = tmp_path / "source.mp4"
    src.write_bytes(b"original")
    ref = s.put(src, KEY)
    peer.objects["library/private/" + KEY]["data"] = b"corrupt!"
    dest = tmp_path / "destination.mp4"
    dest.write_bytes(b"previous valid local copy")
    with pytest.raises(ObjectStoreError, match="checksum"):
        s.download(ref, dest)
    assert dest.read_bytes() == b"previous valid local copy"
    assert not list(tmp_path.glob(".reelvault-s3-*.part"))
    assert peer.bodies[-1].closed


def test_changed_etag_refuses_read_and_delete(store, tmp_path):
    s, peer = store
    src = tmp_path / "source.mp4"
    src.write_bytes(b"original")
    ref = s.put(src, KEY)
    peer.objects["library/private/" + KEY]["ETag"] = '"replacement"'
    with pytest.raises(ObjectStoreError, match="PreconditionFailed"):
        s.open(ref)
    with pytest.raises(ObjectStoreError, match="PreconditionFailed"):
        s.delete(ref)
    assert peer.objects


@pytest.mark.parametrize(
    "key", ["../outside.mp4", "/absolute", KEY + "/more", "x.mp4", "a" * 32 + ".mp4?x"]
)
def test_keys_cannot_escape_namespace(store, key):
    s, peer = store
    with pytest.raises(ValueError):
        s._args(key)
    assert not peer.objects


@pytest.mark.parametrize("start,end", [(-1, None), (8, None), (3, 2), (0, 8)])
def test_ranges_are_bounded(store, tmp_path, start, end):
    s, _ = store
    src = tmp_path / "source.mp4"
    src.write_bytes(b"original")
    ref = s.put(src, KEY)
    with pytest.raises(ValueError):
        s.open(ref, start=start, end=end)


def test_real_s3_multipart_version_range_and_ffprobe(tmp_path, samples):
    endpoint = os.environ.get("REELVAULT_TEST_S3_ENDPOINT")
    if not endpoint:
        pytest.skip("Requires an owned MinIO or S3-compatible test server")
    bucket = "reelvault-test-" + uuid4().hex
    config = S3Config(
        bucket=bucket,
        endpoint=endpoint,
        access_key=os.environ["REELVAULT_TEST_S3_ACCESS_KEY"],
        secret_key=os.environ["REELVAULT_TEST_S3_SECRET_KEY"],
        part_size_mb=5,
    )
    with ObjectStore(config) as s:
        s.client.create_bucket(Bucket=bucket)
        try:
            s.client.put_bucket_versioning(
                Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
            )
            src = tmp_path / "large.mp4"
            # A real playable MP4 with padding exercises three multipart parts.
            data = samples["a"].read_bytes() + b"\0" * (11 * 1024**2)
            src.write_bytes(data)
            ref = s.put(src, KEY)
            assert ref.version_id and "-" in ref.etag
            assert ref.sha256 == hashlib.sha256(data).hexdigest()
            assert ObjectRef.model_validate_json(ref.model_dump_json()) == ref
            with s.open(ref, start=100, end=4095) as body:
                assert body.read() == data[100:4096]
            dest = tmp_path / "restored.mp4"
            s.download(ref, dest)
            assert dest.read_bytes() == data
            import subprocess

            duration = subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=nw=1:nk=1",
                    str(dest),
                ],
                text=True,
            )
            assert float(duration) == pytest.approx(4, abs=0.2)
            with pytest.raises(ObjectStoreError, match="PreconditionFailed"):
                s.put(src, KEY)
            assert not s.client.list_multipart_uploads(Bucket=bucket).get("Uploads")
            s.delete(ref)
            assert not s.client.list_object_versions(Bucket=bucket).get("Versions")
            # A pinned old version remains readable and deleting it preserves a
            # newer external replacement, even when its ETag and bytes differ.
            ref = s.put(src, KEY)
            replacement = s.client.put_object(
                Bucket=bucket, Key=config.prefix + "/" + KEY, Body=b"external replacement"
            )
            with s.open(ref) as body:
                assert body.read() == data
            s.delete(ref)
            versions = s.client.list_object_versions(Bucket=bucket).get("Versions", [])
            assert len(versions) == 1 and versions[0]["VersionId"] == replacement["VersionId"]
        finally:
            # Limit cleanup strictly to the fresh, uniquely owned test bucket.
            versions = s.client.list_object_versions(Bucket=bucket)
            for item in [*versions.get("Versions", []), *versions.get("DeleteMarkers", [])]:
                s.client.delete_object(Bucket=bucket, Key=item["Key"], VersionId=item["VersionId"])
            for item in s.client.list_multipart_uploads(Bucket=bucket).get("Uploads", []):
                s.client.abort_multipart_upload(
                    Bucket=bucket, Key=item["Key"], UploadId=item["UploadId"]
                )
            s.client.delete_bucket(Bucket=bucket)
