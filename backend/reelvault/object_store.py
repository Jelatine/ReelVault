"""Private S3 originals: immutable keys, bounded transfers and verified downloads.

Network operations are synchronous. Request handlers must use a thread or an owned
transfer process, never call these methods directly on the ASGI event loop.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import mimetypes
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import S3Config
from .object_types import KEY, ObjectRef

Progress = Callable[[float], None]
Started = Callable[[str], None]


class ObjectStoreError(OSError):
    """A safe error that does not expose endpoint credentials or signed URLs."""

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.code = code


def _signature(path: Path) -> tuple[int, int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


class ObjectStore:
    def __init__(self, config: S3Config, *, client: Any = None):
        self.config = config
        if client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError:
                raise ObjectStoreError("S3 support requires the optional s3 dependency") from None
            credentials = {}
            for name, value in (
                ("aws_access_key_id", config.access_key),
                ("aws_secret_access_key", config.secret_key),
                ("aws_session_token", config.session_token),
            ):
                if value is not None:
                    credentials[name] = value.get_secret_value()
            client = boto3.client(
                "s3",
                endpoint_url=config.endpoint,
                region_name=config.region,
                config=Config(
                    signature_version="s3v4",
                    connect_timeout=5,
                    read_timeout=30,
                    retries={"mode": "standard", "total_max_attempts": 3},
                    s3={"addressing_style": config.addressing_style},
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_required",
                ),
                **credentials,
            )
        self.client = client
        if config.namespace_id:
            try:
                self.validate_namespace(config.library_id or "", config.namespace_id)
            except Exception:
                self.close()
                raise

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> ObjectStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _args(self, key: str) -> dict[str, str]:
        if KEY.fullmatch(key) is None:
            raise ValueError("Invalid original-video object key")
        return {"Bucket": self.config.bucket, "Key": f"{self.config.prefix}/{key}"}

    def _failure(self, error: Exception) -> ObjectStoreError:
        # SDK exceptions can contain response headers, URLs and credentials.
        response = getattr(error, "response", {})
        code = str(response.get("Error", {}).get("Code", "unavailable"))
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", code):
            code = "unavailable"
        return ObjectStoreError(f"S3 operation failed ({code})", code=code)

    def _namespace(self) -> dict[str, Any]:
        try:
            result = self.client.get_object(
                Bucket=self.config.bucket, Key=self.config.prefix + "/.reelvault-namespace.json"
            )
            with contextlib.closing(result["Body"]) as body:
                data = body.read(1025)
            value = json.loads(data)
            if (
                len(data) > 1024
                or not isinstance(value, dict)
                or set(value) != {"version", "id", "library_id"}
                or type(value["version"]) is not int
                or value["version"] != 1
                or not isinstance(value["id"], str)
                or not isinstance(value["library_id"], str)
                or re.fullmatch(r"[a-f0-9]{32}", value["id"]) is None
                or re.fullmatch(r"[a-f0-9]{32}", value["library_id"]) is None
            ):
                raise ObjectStoreError("S3 storage identity is invalid")
            return value
        except ObjectStoreError:
            raise
        except (ValueError, KeyError, TypeError):
            raise ObjectStoreError("S3 storage identity is invalid") from None
        except Exception as error:
            raise self._failure(error) from None

    def bind_namespace(self, library_id: str) -> str:
        if re.fullmatch(r"[a-f0-9]{32}", library_id) is None:
            raise ValueError("Invalid library identity")
        try:
            value = self._namespace()
        except ObjectStoreError as error:
            if error.code not in {"404", "NoSuchKey", "NotFound"}:
                raise
            data = json.dumps({"version": 1, "id": uuid4().hex, "library_id": library_id}).encode()
            try:
                self.client.put_object(
                    Bucket=self.config.bucket,
                    Key=self.config.prefix + "/.reelvault-namespace.json",
                    Body=data,
                    IfNoneMatch="*",
                    ContentType="application/json",
                    ContentMD5=base64.b64encode(
                        hashlib.md5(data, usedforsecurity=False).digest()
                    ).decode(),
                )
            except Exception as error:
                failed = self._failure(error)
                if failed.code != "PreconditionFailed":
                    raise failed from None
            value = self._namespace()
        if value["library_id"] != library_id:
            raise ObjectStoreError("S3 prefix belongs to another video library")
        return str(value["id"])

    def validate_namespace(self, library_id: str, namespace_id: str) -> None:
        value = self._namespace()
        if value["library_id"] != library_id or value["id"] != namespace_id:
            raise ObjectStoreError("S3 storage identity mismatch")

    def head(self, key: str) -> ObjectRef:
        try:
            value = self.client.head_object(**self._args(key))
            metadata = value.get("Metadata", {})
            if metadata.get("reelvault-format") != "1":
                raise ObjectStoreError("S3 object is not a ReelVault original")
            return ObjectRef(
                key=key,
                size=value["ContentLength"],
                sha256=metadata.get("sha256", ""),
                etag=value["ETag"],
                version_id=value.get("VersionId"),
                modified=value["LastModified"],
                transfer_id=metadata.get("transfer-id"),
            )
        except ObjectStoreError:
            raise
        except Exception as error:
            raise self._failure(error) from None

    def put(
        self,
        source: Path,
        key: str,
        *,
        on_progress: Progress | None = None,
        on_started: Started | None = None,
        transfer_id: str | None = None,
    ) -> ObjectRef:
        args = self._args(key)
        if transfer_id is not None and re.fullmatch(r"[a-f0-9]{32}", transfer_id) is None:
            raise ValueError("Invalid S3 transfer identifier")
        before = _signature(source)
        total = before[2]
        if total <= 0:
            raise ValueError("Original video must not be empty")
        # Each part is bounded, and even multi-gigabyte videos stay below 10,000 parts.
        part_size = max(self.config.part_size_mb * 1024**2, (total + 9999) // 10000)
        if part_size > 128 * 1024**2:
            raise ValueError("Original exceeds the bounded multipart transfer limit")
        digest = hashlib.sha256()
        read = 0
        with source.open("rb") as stream:
            while data := stream.read(4 * 1024**2):
                digest.update(data)
                read += len(data)
                if on_progress:
                    on_progress(read / total * 0.15)
        if _signature(source) != before:
            raise ObjectStoreError("Source changed during S3 transfer")
        sha256 = digest.hexdigest()
        upload_id = None
        try:
            metadata = {"reelvault-format": "1", "sha256": sha256}
            if transfer_id:
                metadata["transfer-id"] = transfer_id
            response = self.client.create_multipart_upload(
                **args,
                ContentType=mimetypes.guess_type(source.name)[0] or "application/octet-stream",
                Metadata=metadata,
            )
            upload_id = response["UploadId"]
            if on_started:
                on_started(upload_id)
            parts: list[dict[str, Any]] = []
            written = 0
            with source.open("rb") as stream:
                while data := stream.read(part_size):
                    number = len(parts) + 1
                    result = self.client.upload_part(
                        **args,
                        UploadId=upload_id,
                        PartNumber=number,
                        Body=data,
                        ContentMD5=base64.b64encode(
                            hashlib.md5(data, usedforsecurity=False).digest()
                        ).decode(),
                    )
                    parts.append({"ETag": result["ETag"], "PartNumber": number})
                    written += len(data)
                    if on_progress:
                        on_progress(0.15 + written / total * 0.8)
            if written != total or _signature(source) != before:
                raise ObjectStoreError("Source changed during S3 transfer")
            # The multipart object is invisible until this atomic, conditional publish.
            self.client.complete_multipart_upload(
                **args,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
                IfNoneMatch="*",
            )
            upload_id = None
            ref = self.head(key)
            if ref.sha256 != sha256 or ref.size != total:
                raise ObjectStoreError("S3 original verification failed")
            if on_progress:
                on_progress(1.0)
            return ref
        except (ObjectStoreError, ValueError):
            raise
        except Exception as error:
            raise self._failure(error) from None
        finally:
            if upload_id is not None:
                with contextlib.suppress(ObjectStoreError):
                    self.abort(key, upload_id)

    def abort(self, key: str, upload_id: str) -> None:
        try:
            self.client.abort_multipart_upload(**self._args(key), UploadId=upload_id)
        except Exception as error:
            response = getattr(error, "response", {})
            if response.get("Error", {}).get("Code") != "NoSuchUpload":
                raise self._failure(error) from None

    def open(self, ref: ObjectRef, *, start: int = 0, end: int | None = None) -> Any:
        if not 0 <= start < ref.size or (end is not None and not start <= end < ref.size):
            raise ValueError("Invalid S3 byte range")
        args: dict[str, Any] = {**self._args(ref.key), "IfMatch": ref.etag}
        if ref.version_id:
            args["VersionId"] = ref.version_id
        if start or end is not None:
            args["Range"] = f"bytes={start}-{end if end is not None else ''}"
        try:
            value = self.client.get_object(**args)
            expected = (end if end is not None else ref.size - 1) - start + 1
            if (
                value["ContentLength"] != expected
                or value.get("ETag") != ref.etag
                or (ref.version_id and value.get("VersionId") != ref.version_id)
            ):
                value["Body"].close()
                raise ObjectStoreError("S3 object identity or size changed")
            return value["Body"]
        except ObjectStoreError:
            raise
        except Exception as error:
            raise self._failure(error) from None

    def download(
        self,
        ref: ObjectRef,
        destination: Path,
        *,
        on_progress: Progress | None = None,
    ) -> None:
        staged = destination.parent / f".reelvault-s3-{uuid4().hex}.part"
        digest = hashlib.sha256()
        written = 0
        try:
            with contextlib.closing(self.open(ref)) as incoming, staged.open("xb") as outgoing:
                while chunk := incoming.read(4 * 1024**2):
                    digest.update(chunk)
                    outgoing.write(chunk)
                    written += len(chunk)
                    if on_progress:
                        on_progress(min(0.99, written / ref.size))
                outgoing.flush()
                os.fsync(outgoing.fileno())
            if written != ref.size or digest.hexdigest() != ref.sha256:
                raise ObjectStoreError("S3 original checksum verification failed")
            timestamp = ref.modified.timestamp()
            os.utime(staged, (timestamp, timestamp))
            staged.replace(destination)
            if on_progress:
                on_progress(1.0)
        except (ObjectStoreError, ValueError):
            raise
        except Exception as error:
            raise self._failure(error) from None
        finally:
            staged.unlink(missing_ok=True)

    def delete(self, ref: ObjectRef) -> None:
        args: dict[str, Any] = {**self._args(ref.key), "IfMatch": ref.etag}
        if ref.version_id:
            args["VersionId"] = ref.version_id
        try:
            self.client.delete_object(**args)
        except Exception as error:
            raise self._failure(error) from None
