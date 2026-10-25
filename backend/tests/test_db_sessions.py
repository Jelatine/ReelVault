from __future__ import annotations

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi.testclient import TestClient

from reelvault.main import create_app

from .conftest import HEADERS, login, upload_ready


class BodyProbe:
    """Records how many pooled connections are checked out while bodies are sent."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.checked_out: list[int] = []

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        async def probe(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.body" and scope["path"].endswith("/stream"):
                engine = self.app.state.sessionmaker.kw["bind"]
                self.checked_out.append(engine.pool.checkedout())
            await send(message)

        await self.app(scope, receive, probe)


def test_streams_release_their_connection_before_the_body(settings, samples):
    # Seeking opens many long range responses; each must not pin a pool connection.
    app = create_app(settings)
    probe = BodyProbe(app)
    with TestClient(probe, headers=HEADERS) as client:
        login(client)
        video = upload_ready(client, samples["a"])
        r = client.get(video["stream_url"], headers={"Range": "bytes=0-"})
        assert r.status_code == 206
    assert probe.checked_out and max(probe.checked_out) == 0


def test_upload_chunk_waits_for_the_writer_without_blocking_other_requests(client, settings):
    up = client.post("/api/uploads", json={"filename": "x.mp4", "size": 1024}).json()
    writer = sqlite3.connect(settings.db_path, timeout=1)
    writer.execute("BEGIN IMMEDIATE")  # e.g. a job finishing its transaction
    with ThreadPoolExecutor(1) as pool:
        chunk = pool.submit(client.put, f"/api/uploads/{up['id']}?offset=0", content=b"\0" * 1024)
        time.sleep(0.5)
        started = time.monotonic()
        assert client.get("/healthz").status_code == 200
        assert time.monotonic() - started < 2
        writer.rollback()
        assert chunk.result(timeout=30).json()["received"] == 1024
    writer.close()
