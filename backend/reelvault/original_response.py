"""Serve originals: the verified local cache, or a pinned-version S3 Range proxy.

Clients only ever talk to this server; object-store credentials and the private
endpoint never leave it. Callers perform authorization before calling here.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import AsyncIterator, Generator
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from .config import Settings
from .errors import APIError
from .locations import location_of
from .object_types import ObjectRef

CHUNK = 1024 * 1024
RANGE = re.compile(r"bytes=(\d*)-(\d*)")


def _byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """One satisfiable range, or None for the whole object; multi-range is ignored."""
    if not header:
        return None
    match = RANGE.fullmatch(header.strip())
    if match is None:
        return None
    first, last = match.groups()
    if not first and not last:
        return None
    if not first:
        length = int(last)
        if length == 0:
            raise APIError(416, "请求范围无效", code="range_not_satisfiable")
        return max(0, size - length), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        raise APIError(416, "请求范围无效", code="range_not_satisfiable")
    return start, end


def _unavailable() -> APIError:
    return APIError(503, "对象存储暂不可用，请稍后重试", code="object_storage_unavailable")


def original_response(
    request: Request,
    settings: Settings,
    rel: str | None,
    local: Path,
    *,
    media_type: str | None = None,
    filename: str | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    from .object_library import bound_config, cache_valid

    ref: ObjectRef | None = (
        settings.s3_objects.get(rel) if rel and location_of(rel) == "s3" else None
    )
    if ref is None or cache_valid(local, ref):
        if not local.is_file():
            raise APIError(404, "视频文件丢失", code="video_file_missing")
        return FileResponse(local, media_type=media_type, filename=filename, headers=headers)

    from .object_store import ObjectStore, ObjectStoreError

    span = _byte_range(request.headers.get("range"), ref.size)
    start, end = span or (0, ref.size - 1)
    out: dict[str, str] = {
        "ETag": f'"{ref.sha256}"',
        **(headers or {}),
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
    }
    if filename is not None:
        # Same disposition FileResponse would send.
        out["Content-Disposition"] = FileResponse(local, filename=filename).headers[
            "content-disposition"
        ]
    if span is not None:
        out["Content-Range"] = f"bytes {start}-{end}/{ref.size}"
    status = 206 if span is not None else 200
    if request.method == "HEAD":
        return Response(status_code=status, headers=out, media_type=media_type)
    try:
        assert rel is not None
        config = bound_config(settings, rel.split("/")[1])
        store = ObjectStore(config)
    except (ObjectStoreError, ImportError) as error:
        raise _unavailable() from error
    try:
        body: Any = store.open(ref, start=start, end=end)
    except ObjectStoreError as error:
        store.close()
        raise _unavailable() from error

    def chunks() -> Generator[bytes]:
        # Closing the generator on client disconnect releases the network response.
        try:
            while data := body.read(CHUNK):
                yield data
        finally:
            with contextlib.suppress(Exception):
                body.close()
            store.close()

    async def stream() -> AsyncIterator[bytes]:
        source = chunks()
        try:
            async for data in iterate_in_threadpool(source):
                yield data
        finally:
            await run_in_threadpool(source.close)

    return StreamingResponse(stream(), status_code=status, headers=out, media_type=media_type)


def local_original(settings: Settings, rel: str) -> Path:
    """The original as a readable local file, for request-time ffmpeg readers.

    Archived S3 originals without a verified local copy are not fetched inside a
    request: the client is told to fetch one through the controllable job.
    """
    from .library import abs_path
    from .object_library import cache_valid

    path = abs_path(settings, rel)
    ref = settings.s3_objects.get(rel) if location_of(rel) == "s3" else None
    if ref is not None and not cache_valid(path, ref):
        raise APIError(
            409,
            "原视频保存在对象存储中，请先在视频详情下载本地副本",
            code="original_not_cached",
        )
    return path
