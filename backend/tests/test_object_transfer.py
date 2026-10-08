from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

import pytest

from reelvault.config import S3Config
from reelvault.media import object_transfer
from reelvault.media.ffmpeg import Canceled, ProcessHandle
from reelvault.object_store import ObjectStore, ObjectStoreError

from .test_object_store import KEY, ProtocolClient


def test_private_worker_request_does_not_put_credentials_in_arguments(tmp_path):
    config = S3Config(bucket="private-library", access_key="test-access", secret_key="test-secret")
    path = object_transfer._request(config, {"operation": "get"}, tmp_path)
    assert path.stat().st_mode & 0o777 == 0o600
    value = json.loads(path.read_text())
    assert value["config"]["secret_key"] == "test-secret"
    assert "test-secret" not in str(path)
    assert value["operation"] == "get"


@pytest.mark.parametrize("completed", [False, True])
def test_restart_reconciles_only_its_own_upload(monkeypatch, tmp_path, completed):
    peer = ProtocolClient()
    config = S3Config(bucket="private-library")
    token = uuid4().hex
    directory = tmp_path / ("s3-transfer-" + token)
    directory.mkdir(mode=0o700)
    object_transfer._request(
        config,
        {
            "operation": "put",
            "key": KEY,
            "transfer_id": token,
        },
        directory,
    )
    source = tmp_path / "source.mp4"
    source.write_bytes(b"original")
    if completed:
        ref = ObjectStore(config, client=peer).put(source, KEY, transfer_id=token)
        (directory / "state.json").write_text(json.dumps({"ref": ref.model_dump(mode="json")}))
    else:
        upload_id = peer.create_multipart_upload(
            Bucket=config.bucket,
            Key=config.prefix + "/" + KEY,
            ContentType="video/mp4",
            Metadata={},
        )["UploadId"]
        (directory / "state.json").write_text(json.dumps({"upload_id": upload_id}))
    monkeypatch.setattr(object_transfer, "ObjectStore", lambda c: ObjectStore(c, client=peer))
    assert asyncio.run(object_transfer.recover_transfers(config, tmp_path)) == 1
    assert not peer.objects and not peer.uploads and not directory.exists()
    assert source.read_bytes() == b"original"


def test_failed_recovery_removes_secret_and_can_retry(monkeypatch, tmp_path):
    peer = ProtocolClient()
    config = S3Config(
        bucket="private-library", access_key="private-key", secret_key="private-secret"
    )
    directory = tmp_path / "s3-transfer-failed"
    directory.mkdir(mode=0o700)
    object_transfer._request(
        config, {"operation": "put", "key": KEY, "transfer_id": uuid4().hex}, directory
    )

    def offline(config):
        raise ObjectStoreError("offline")

    monkeypatch.setattr(object_transfer, "ObjectStore", offline)
    assert asyncio.run(object_transfer.recover_transfers(config, tmp_path)) == 0
    assert not (directory / "request.json").exists()
    journal = (directory / "recovery.json").read_text()
    assert "private-secret" not in journal and "private-key" not in journal
    monkeypatch.setattr(object_transfer, "ObjectStore", lambda c: ObjectStore(c, client=peer))
    assert asyncio.run(object_transfer.recover_transfers(config, tmp_path)) == 1
    assert not directory.exists()


def test_recovery_does_not_delete_preexisting_object_or_other_namespace(monkeypatch, tmp_path):
    peer = ProtocolClient()
    config = S3Config(bucket="private-library")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"pre-existing original")
    store = ObjectStore(config, client=peer)
    original = store.put(source, KEY, transfer_id=uuid4().hex)
    directory = tmp_path / "s3-transfer-conflict"
    directory.mkdir(mode=0o700)
    object_transfer._request(
        config, {"operation": "put", "key": KEY, "transfer_id": uuid4().hex}, directory
    )
    other = tmp_path / "s3-transfer-other"
    other.mkdir(mode=0o700)
    object_transfer._request(
        S3Config(bucket="other-library"),
        {"operation": "put", "key": KEY, "transfer_id": uuid4().hex},
        other,
    )
    monkeypatch.setattr(object_transfer, "ObjectStore", lambda c: ObjectStore(c, client=peer))
    assert asyncio.run(object_transfer.recover_transfers(config, tmp_path)) == 1
    assert store.head(KEY) == original and other.exists() and not directory.exists()


@pytest.fixture
def real_store():
    endpoint = os.environ.get("REELVAULT_TEST_S3_ENDPOINT")
    if not endpoint:
        pytest.skip("Requires an owned MinIO or S3-compatible server")
    bucket = "reelvault-test-" + uuid4().hex
    config = S3Config(
        bucket=bucket,
        endpoint=endpoint,
        part_size_mb=5,
        access_key=os.environ["REELVAULT_TEST_S3_ACCESS_KEY"],
        secret_key=os.environ["REELVAULT_TEST_S3_SECRET_KEY"],
    )
    with ObjectStore(config) as store:
        store.client.create_bucket(Bucket=bucket)
        try:
            yield store
        finally:
            for entry in store.client.list_objects_v2(Bucket=bucket).get("Contents", []):
                store.client.delete_object(Bucket=bucket, Key=entry["Key"])
            for entry in store.client.list_multipart_uploads(Bucket=bucket).get("Uploads", []):
                store.client.abort_multipart_upload(
                    Bucket=bucket, Key=entry["Key"], UploadId=entry["UploadId"]
                )
            store.client.delete_bucket(Bucket=bucket)


def test_real_worker_upload_download_progress_and_private_cleanup(real_store, tmp_path, samples):
    store = real_store
    source = samples["a"]
    progress = []

    async def run():
        ref = await object_transfer.upload_original(
            store.config,
            source,
            KEY,
            temporary_root=tmp_path,
            handle=ProcessHandle(),
            on_progress=progress.append,
        )
        destination = tmp_path / "verified.mp4"
        await object_transfer.download_original(
            store.config, ref, destination, handle=ProcessHandle()
        )
        assert destination.read_bytes() == source.read_bytes()
        return ref

    ref = asyncio.run(run())
    assert progress == sorted(progress) and progress[-1] == 1
    assert ref.transfer_id and not list(tmp_path.glob("s3-transfer-*"))
    assert not store.client.list_multipart_uploads(Bucket=store.config.bucket).get("Uploads")
    store.delete(ref)


def test_real_worker_pause_resume_and_cancel_preserves_original(real_store, tmp_path):
    store = real_store
    source = tmp_path / "large.mp4"
    with source.open("wb") as stream:
        for _ in range(32):
            stream.write(b"a" * 1024**2)
    handle = ProcessHandle()
    paused = asyncio.Event()
    values = []

    def progress(fraction):
        values.append(fraction)
        states = list(tmp_path.glob("s3-transfer-*/state.json"))
        if (
            not paused.is_set()
            and fraction >= 0.15
            and states
            and "upload_id" in json.loads(states[0].read_text())
        ):
            handle.pause()
            paused.set()

    async def run():
        task = asyncio.create_task(
            object_transfer.upload_original(
                store.config,
                source,
                KEY,
                temporary_root=tmp_path,
                handle=handle,
                on_progress=progress,
            )
        )
        try:
            await asyncio.wait_for(paused.wait(), timeout=20)
            count = len(values)
            await asyncio.sleep(0.25)
            assert len(values) == count and not task.done()
            # The process is genuinely stopped, while the ASGI-style event loop runs.
            assert handle.paused and handle.process is not None
            uploads = await asyncio.to_thread(
                store.client.list_multipart_uploads, Bucket=store.config.bucket
            )
            assert uploads.get("Uploads"), "Pause must exercise an actual remote multipart upload"
            handle.resume()
            await asyncio.sleep(0.01)
            handle.cancel()
            with pytest.raises(Canceled):
                await asyncio.wait_for(task, timeout=30)
        finally:
            if not task.done():
                handle.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert source.stat().st_size == 32 * 1024**2
    assert not list(tmp_path.glob("s3-transfer-*"))
    assert not store.client.list_objects_v2(Bucket=store.config.bucket).get("Contents")
    assert not store.client.list_multipart_uploads(Bucket=store.config.bucket).get("Uploads")
    assert handle.process is None


def test_real_worker_cancel_download_preserves_previous_file(real_store, tmp_path):
    store = real_store
    source = tmp_path / "large.mp4"
    source.write_bytes(b"a" * (11 * 1024**2))
    ref = store.put(source, KEY)
    destination = tmp_path / "previous.mp4"
    destination.write_bytes(b"previous verified original")
    handle = ProcessHandle()
    paused = asyncio.Event()

    def progress(fraction):
        if not paused.is_set():
            handle.pause()
            paused.set()

    async def run():
        task = asyncio.create_task(
            object_transfer.download_original(
                store.config,
                ref,
                destination,
                handle=handle,
                on_progress=progress,
            )
        )
        try:
            await asyncio.wait_for(paused.wait(), timeout=20)
            assert handle.process is not None
            parts = list(tmp_path.glob("s3-transfer-*/.reelvault-s3-*.part"))
            assert parts, "Cancellation must exercise a partially downloaded original"
            await asyncio.sleep(0.05)
            handle.cancel()
            with pytest.raises(Canceled):
                await asyncio.wait_for(task, timeout=20)
        finally:
            if not task.done():
                handle.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert destination.read_bytes() == b"previous verified original"
    assert not list(tmp_path.glob("s3-transfer-*")) and handle.process is None
    assert store.head(KEY) == ref
    store.delete(ref)


def test_real_cancel_after_publication_removes_only_owned_original(real_store, tmp_path, samples):
    store = real_store
    handle = ProcessHandle()
    reached_publication = False

    def progress(fraction):
        nonlocal reached_publication
        if fraction == 1:
            reached_publication = True
            handle.cancel()

    async def run():
        with pytest.raises(Canceled):
            await object_transfer.upload_original(
                store.config,
                samples["a"],
                KEY,
                temporary_root=tmp_path,
                handle=handle,
                on_progress=progress,
            )

    asyncio.run(run())
    assert reached_publication and not list(tmp_path.glob("s3-transfer-*"))
    assert not store.client.list_objects_v2(Bucket=store.config.bucket).get("Contents")
    assert not store.client.list_multipart_uploads(Bucket=store.config.bucket).get("Uploads")
