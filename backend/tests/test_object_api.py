"""Original-video API behavior for S3 originals with an absent or verified local cache."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from reelvault import object_library, object_store
from reelvault.config import S3Config, Settings
from reelvault.library import abs_path
from reelvault.main import create_app
from reelvault.models import OriginalObject, Video
from reelvault.object_store import ObjectStore

from .conftest import HEADERS, login
from .test_object_library import NamespacePeer
from .test_object_store import KEY, S3Failure

DATA = bytes(range(256)) * 40


@pytest.fixture
def s3_app(settings: Settings, monkeypatch, tmp_path) -> Iterator[tuple[TestClient, Any, str]]:
    settings.s3 = S3Config(
        bucket="test-originals", access_key="private-key", secret_key="private-secret"
    )
    peer = NamespacePeer()

    def store(config: S3Config) -> ObjectStore:
        return ObjectStore(config, client=peer)

    monkeypatch.setattr(object_library, "ObjectStore", store)
    monkeypatch.setattr(object_store, "ObjectStore", store)
    app = create_app(settings)
    with TestClient(app, headers=HEADERS) as client:
        login(client)
        assert settings.s3_current
        source = tmp_path / "source.mp4"
        source.write_bytes(DATA)
        sessions = app.state.sessionmaker
        with sessions() as db:
            path = object_library.reserve_original(db, settings, KEY)
            db.commit()
            token = db.get(OriginalObject, path).transfer_id
        config = object_library.bound_config(settings, settings.s3_current)
        ref = ObjectStore(config, client=peer).put(source, KEY, transfer_id=token)
        with sessions() as db:
            object_library.adopt_original(db, settings, path, ref)
            video = Video(
                title="clip",
                original_name="clip.mp4",
                file_path=path,
                size=len(DATA),
                status="ready",
                container="mp4",
                video_codec="h264",
                audio_codec="aac",
            )
            db.add(video)
            db.commit()
            video_id = video.id
        yield client, peer, video_id


def remote_key(settings: Settings) -> str:
    assert settings.s3 is not None
    return settings.s3.prefix + "/" + KEY


def test_uncached_original_streams_pinned_ranges(s3_app, settings):
    client, peer, video_id = s3_app
    assert not abs_path(settings, next(iter(settings.s3_objects))).exists()
    whole = client.get(f"/api/videos/{video_id}/stream")
    assert whole.status_code == 200 and whole.content == DATA
    assert whole.headers["accept-ranges"] == "bytes"
    part = client.get(f"/api/videos/{video_id}/stream", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == DATA[10:20]
    assert part.headers["content-range"] == f"bytes 10-19/{len(DATA)}"
    tail = client.get(f"/api/videos/{video_id}/stream", headers={"Range": "bytes=-5"})
    assert tail.status_code == 206 and tail.content == DATA[-5:]
    bad = client.get(f"/api/videos/{video_id}/stream", headers={"Range": f"bytes={len(DATA)}-"})
    assert bad.status_code == 416
    download = client.get(f"/api/videos/{video_id}/download")
    assert download.content == DATA
    assert "attachment" in download.headers["content-disposition"]
    for response in (whole, part, download):
        assert "test-originals" not in str(response.headers)
        assert "private" not in response.headers.get("location", "")
    assert all(body.closed for body in peer.bodies)


def test_offline_object_store_reports_unavailable(s3_app, monkeypatch):
    client, peer, video_id = s3_app

    def offline(**_: Any) -> None:
        raise S3Failure("ServiceUnavailable")

    monkeypatch.setattr(peer, "get_object", offline)
    response = client.get(f"/api/videos/{video_id}/stream")
    assert response.status_code == 503
    assert response.json()["code"] == "object_storage_unavailable"


def test_verified_cache_is_served_without_network(s3_app, settings, monkeypatch):
    client, peer, video_id = s3_app
    path, ref = next(iter(settings.s3_objects.items()))
    local = abs_path(settings, path)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(DATA)
    object_library.mark_cache(local, ref)
    monkeypatch.setattr(
        peer, "get_object", lambda **_: pytest.fail("A verified cache must not use S3")
    )
    response = client.get(f"/api/videos/{video_id}/stream", headers={"Range": "bytes=0-3"})
    assert response.status_code == 206 and response.content == DATA[:4]


def test_permanent_delete_removes_remote_original(s3_app, settings):
    client, peer, video_id = s3_app
    key = remote_key(settings)
    assert key in peer.objects
    assert client.delete(f"/api/videos/{video_id}?permanent=true").status_code == 200
    assert key not in peer.objects and not settings.s3_objects
    assert client.get(f"/api/videos/{video_id}").status_code == 404


def test_offline_delete_keeps_video_and_object(s3_app, settings, monkeypatch):
    client, peer, video_id = s3_app

    def offline(**_: Any) -> None:
        raise S3Failure("ServiceUnavailable")

    monkeypatch.setattr(peer, "delete_object", offline)
    assert client.delete(f"/api/videos/{video_id}").status_code == 200  # Trash only.
    for response in (
        client.delete(f"/api/videos/{video_id}"),
        client.post("/api/trash/empty"),
        client.post("/api/videos/batch", json={"ids": [video_id], "action": "purge"}),
    ):
        assert response.status_code == 503
        assert response.json()["code"] == "object_storage_unavailable"
    assert remote_key(settings) in peer.objects and settings.s3_objects
    assert client.post(f"/api/videos/{video_id}/restore").status_code == 200


def test_share_download_and_webdav_proxy_uncached_original(s3_app, settings):
    from .test_shares import create, guest
    from .test_webdav import credential, href, listing

    client, peer, video_id = s3_app
    _, prefix = create(client, video_id, allow_download=True)
    visitor = guest(client)
    shared = visitor.get(f"{prefix}/videos/{video_id}/stream", headers={"Range": "bytes=5-9"})
    assert shared.status_code == 206 and shared.content == DATA[5:10]
    assert shared.headers["cache-control"] == "no-store"
    assert shared.headers["x-robots-tag"] == "noindex, nofollow"
    downloaded = visitor.get(f"{prefix}/videos/{video_id}/download")
    assert downloaded.status_code == 200 and downloaded.content == DATA
    auth = credential(client)
    path = href(listing(client, "/dav/all/", auth)[1])
    dav = client.get(path, headers={**auth, "Range": "bytes=0-7"})
    assert dav.status_code == 206 and dav.content == DATA[:8]
    head = client.head(path, headers=auth)
    assert head.status_code == 200 and head.headers["content-length"] == str(len(DATA))
    assert all(body.closed for body in peer.bodies)


def test_locations_report_s3_without_secrets(s3_app, settings):
    client, _, _ = s3_app
    settings.s3 = settings.s3.model_copy(update={"endpoint": "https://private-s3.internal:9000"})
    response = client.get("/api/system/locations")
    entry = next(item for item in response.json()["items"] if item["id"] == "s3")
    assert entry["kind"] == "s3" and entry["video_count"] == 1
    assert entry["total"] is None and entry["free"] is None
    for secret in ("private-key", "private-secret", "private-s3.internal"):
        assert secret not in response.text
    # Changing the endpoint unbinds the namespace rather than reusing it elsewhere.
    assert entry["available"] is False and entry["error"]
