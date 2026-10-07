from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from reelvault.auth import hash_password
from reelvault.models import Collection, CollectionItem, ShareGrant, ShareLink, User, Video, utcnow

from .conftest import HEADERS


def seed(client, settings, title="private"):
    with client.app.state.sessionmaker() as db:
        video = Video(
            title=title,
            file_path="library/test.mp4",
            status="ready",
            container="mp4",
            video_codec="h264",
            description="private notes",
            custom_fields={"private": "value"},
            meta={"gps": "private"},
        )
        db.add(video)
        db.commit()
        vid = video.id
        (settings.library_dir / "test.mp4").write_bytes(b"0123456789")
        return vid


def create(client, vid, **options):
    result = client.post("/api/shares", json={"video_id": vid, **options})
    assert result.status_code == 200, result.text
    record = result.json()
    return record, "/api/public/shares/" + record["url"].rsplit("/", 1)[1]


def guest(client):
    # Use a separate cookie jar while sharing the same initialized app/database.
    return TestClient(client.app, headers=HEADERS)


def test_password_grants_do_not_authenticate_library_and_apply_to_all_media(client, settings):
    vid = seed(client, settings)
    record, prefix = create(client, vid, password="guest-secret", expires_hours=1)
    assert client.get("/api/shares").headers["cache-control"] == "no-store"
    visitor = guest(client)
    assert visitor.get(prefix).status_code == 403
    for resource in ["stream", "download", "poster.jpg"]:
        assert visitor.get(f"{prefix}/videos/{vid}/{resource}").status_code == 403
    response = visitor.post(prefix + "/unlock", json={"password": "incorrect"})
    assert response.status_code == 403
    response = visitor.post(prefix + "/unlock", json={"password": "guest-secret"})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and f"Path={prefix}" in cookie
    public = visitor.get(prefix)
    assert public.status_code == 200 and public.headers["cache-control"] == "no-store"
    item = public.json()["items"][0]
    assert set(item) == {
        "id",
        "title",
        "duration",
        "width",
        "height",
        "stream_url",
        "playback_ready",
        "poster_url",
        "download_url",
    }
    assert item["download_url"] is None
    stream = visitor.get(item["stream_url"], headers={"Range": "bytes=2-5"})
    assert stream.status_code == 206 and stream.content == b"2345"
    assert stream.headers["cache-control"] == "no-store"
    assert visitor.get(f"{prefix}/videos/{vid}/download").status_code == 403
    assert visitor.get(f"/api/videos/{vid}/stream").status_code == 401
    assert visitor.get("/api/videos").status_code == 401
    assert (
        visitor.post(
            f"/api/videos/{vid}/edit", json={"edit": {"op": "rotate", "degrees": 90}}
        ).status_code
        == 401
    )
    other_record, other = create(client, vid, password="guest-secret")
    assert visitor.get(other).status_code == 403
    stolen_grant = visitor.cookies.get("rv_share_" + record["id"])
    assert (
        visitor.get(
            other, headers={"Cookie": f"rv_share_{other_record['id']}={stolen_grant}"}
        ).status_code
        == 403
    )
    with client.app.state.sessionmaker() as db:
        grant = db.scalar(select(ShareGrant))
        assert grant.expires_at <= db.get(ShareLink, record["id"]).expires_at
        assert grant.token_hash not in cookie
    assert client.delete("/api/shares/" + record["id"]).status_code == 200
    assert visitor.get(item["stream_url"]).status_code == 404
    with client.app.state.sessionmaker() as db:
        assert db.scalar(select(ShareGrant)) is None


def test_public_download_expiry_soft_delete_and_unrelated_video(client, settings):
    vid = seed(client, settings)
    other = seed(client, settings, "unrelated")
    record, prefix = create(client, vid, allow_download=True)
    visitor = guest(client)
    item = visitor.get(prefix).json()["items"][0]
    response = visitor.get(item["download_url"])
    assert response.status_code == 200 and response.content == b"0123456789"
    assert "attachment" in response.headers["content-disposition"]
    assert visitor.get(f"{prefix}/videos/{other}/stream").status_code == 404
    assert visitor.get(f"{prefix}/videos/{vid}/../../stream").status_code != 200
    assert visitor.get(f"{prefix}/videos/{vid}/thumbnails.vtt").status_code == 404
    with client.app.state.sessionmaker() as db:
        db.get(Video, vid).deleted_at = utcnow()
        db.commit()
    assert visitor.get(prefix).status_code == 404
    assert visitor.get(item["stream_url"]).status_code == 404
    with client.app.state.sessionmaker() as db:
        db.get(Video, vid).deleted_at = None
        db.get(ShareLink, record["id"]).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert visitor.get(prefix).status_code == 404
    assert visitor.post(prefix + "/unlock", json={"password": "whatever"}).status_code == 404
    assert visitor.get("/api/public/shares/not-a-real-token").status_code == 404


def test_collection_membership_rechecked_and_owner_management_isolated(client, settings):
    first = seed(client, settings, "first")
    second = seed(client, settings, "second")
    with client.app.state.sessionmaker() as db:
        collection = Collection(name="shared")
        db.add(collection)
        db.flush()
        db.add(CollectionItem(collection_id=collection.id, video_id=first, position=0))
        db.add(CollectionItem(collection_id=collection.id, video_id=second, position=1))
        db.commit()
        collection_id = collection.id
    response = client.post("/api/shares", json={"collection_id": collection_id})
    assert response.status_code == 200
    record = response.json()
    prefix = "/api/public/shares/" + record["url"].rsplit("/", 1)[1]
    visitor = guest(client)
    assert [v["title"] for v in visitor.get(prefix).json()["items"]] == ["first", "second"]
    assert client.delete(f"/api/collections/{collection_id}/items/{first}").status_code == 200
    assert visitor.get(f"{prefix}/videos/{first}/stream").status_code == 404
    assert visitor.get(f"{prefix}/videos/{second}/stream").status_code == 200
    with client.app.state.sessionmaker() as db:
        db.add(User(username="other", password_hash=hash_password("other-secret")))
        db.commit()
    other = guest(client)
    assert (
        other.post(
            "/api/auth/login", json={"username": "other", "password": "other-secret"}
        ).status_code
        == 200
    )
    assert other.get("/api/shares").json() == []
    assert other.delete("/api/shares/" + record["id"]).status_code == 404
    assert visitor.get(prefix).status_code == 200
    client.delete(f"/api/collections/{collection_id}")
    assert visitor.get(prefix).status_code == 404


def test_password_lockout_csrf_and_invalid_target(client, settings):
    vid = seed(client, settings)
    assert client.post("/api/shares", json={}).status_code == 422
    assert client.post("/api/shares", json={"video_id": vid, "collection_id": 1}).status_code == 422
    assert client.post("/api/shares", json={"video_id": vid, "expires_hours": 0}).status_code == 422
    assert client.post("/api/shares", json={"video_id": vid, "password": "tiny"}).status_code == 422
    _, prefix = create(client, vid, password="guest-secret")
    visitor = guest(client)
    for _ in range(5):
        assert visitor.post(prefix + "/unlock", json={"password": "bad"}).status_code == 403
    assert visitor.post(prefix + "/unlock", json={"password": "guest-secret"}).status_code == 429
    assert (
        visitor.post(
            prefix + "/unlock",
            json={"password": "guest-secret"},
            headers={"X-Forwarded-For": "another-ip"},
        ).status_code
        == 429
    )
    plain = TestClient(client.app)
    csrf = plain.post(prefix + "/unlock", json={"password": "guest-secret"})
    assert csrf.status_code == 403
    assert csrf.headers["cache-control"] == "no-store"


def test_share_migration_round_trip_preserves_media_and_drops_grants(tmp_path):
    from alembic import command
    from sqlalchemy import text

    from reelvault.db import make_engine
    from reelvault.migrate import alembic_config

    engine = make_engine(tmp_path / "legacy-share.db")
    cfg = alembic_config(str(engine.url))
    command.upgrade(cfg, "0025")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, password_hash, created_at, password_changed_at) "
                "VALUES (1, 'owner', 'hash', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    command.upgrade(cfg, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT username FROM users")) == "owner"
        assert connection.scalar(text("SELECT count(*) FROM share_links")) == 0
    command.downgrade(cfg, "0025")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT username FROM users")) == "owner"
        assert "share_links" not in {
            row[0]
            for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
    command.upgrade(cfg, "head")
    engine.dispose()


def test_backup_preserves_share_links_but_revokes_visitor_grants(client, settings, tmp_path):
    import shutil

    from reelvault.backup import create_backup, restore_backup
    from reelvault.config import Settings
    from reelvault.db import make_engine, make_sessionmaker

    vid = seed(client, settings)
    record, prefix = create(client, vid, password="guest-secret")
    visitor = guest(client)
    assert visitor.post(prefix + "/unlock", json={"password": "guest-secret"}).status_code == 200
    archive = create_backup(settings)
    target = Settings(data_dir=tmp_path / "restored", update_check=False)
    target.ensure_dirs()
    shutil.copytree(settings.library_dir, target.library_dir, dirs_exist_ok=True)
    restore_backup(archive, target)
    engine = make_engine(target.db_path)
    with make_sessionmaker(engine)() as db:
        link = db.get(ShareLink, record["id"])
        assert link.token == record["url"].rsplit("/", 1)[1]
        assert link.password_hash and link.expires_at > utcnow()
        assert db.scalar(select(ShareGrant)) is None
    engine.dispose()


def test_expired_visitor_grant_and_missing_disk_cannot_access_media(client, settings, tmp_path):
    from reelvault.locations import register_root

    root = tmp_path / "shared-drive"
    root.mkdir()
    key, entry = register_root(settings, root, "share drive")
    settings.storage_locations[key] = entry
    with client.app.state.sessionmaker() as db:
        video = Video(
            title="external",
            file_path=f"volumes/{key}/video.mp4",
            status="ready",
            container="mp4",
            video_codec="h264",
        )
        db.add(video)
        db.commit()
        vid = video.id
    (root / "library" / "video.mp4").write_bytes(b"external bytes")
    _, prefix = create(client, vid, password="guest-secret", allow_download=True)
    visitor = guest(client)
    visitor.post(prefix + "/unlock", json={"password": "guest-secret"})
    stream = f"{prefix}/videos/{vid}/stream"
    assert visitor.get(stream).status_code == 200
    with client.app.state.sessionmaker() as db:
        db.scalar(select(ShareGrant)).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert visitor.get(stream).status_code == 403
    visitor.post(prefix + "/unlock", json={"password": "guest-secret"})
    root.rename(tmp_path / "disconnected-drive")
    offline = visitor.get(stream)
    assert offline.status_code == 503
    assert offline.headers["cache-control"] == "no-store"
    assert visitor.get(f"{prefix}/videos/{vid}/download").status_code == 503
    assert not root.exists()


def test_concurrent_share_creation_obeys_owner_limit(client, settings):
    from concurrent.futures import ThreadPoolExecutor

    vid = seed(client, settings)
    with client.app.state.sessionmaker() as db:
        owner_id = db.scalar(select(User.id).where(User.username == "admin"))
        db.add_all(
            [
                ShareLink(
                    owner_id=owner_id,
                    token=f"preexisting-{index}",
                    video_id=vid,
                    expires_at=utcnow() + timedelta(hours=1),
                )
                for index in range(199)
            ]
        )
        db.commit()

    def submit(_):
        return client.post("/api/shares", json={"video_id": vid}).status_code

    with ThreadPoolExecutor(max_workers=3) as pool:
        assert sorted(pool.map(submit, range(3))) == [200, 400, 400]
    assert len(client.get("/api/shares").json()) == 200
