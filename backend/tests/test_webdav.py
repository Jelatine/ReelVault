from __future__ import annotations

import base64
import json
import os
from urllib.parse import unquote
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy import select

from reelvault.models import Collection, CollectionItem, Folder, RuntimeSetting, Video

DAV = "{DAV:}"


def credential(client):
    response = client.post("/api/system/webdav/credential")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    password = response.json()["password"]
    assert client.put("/api/system/webdav", json={"enabled": True}).status_code == 200
    return {"Authorization": "Basic " + base64.b64encode(f"reelvault:{password}".encode()).decode()}


def listing(client, path, auth, depth="1", content=b""):
    response = client.request("PROPFIND", path, headers={**auth, "Depth": depth}, content=content)
    assert response.status_code == 207, response.text
    return ET.fromstring(response.content).findall(DAV + "response")


def href(row):
    return row.findtext(DAV + "href")


@pytest.fixture
def media(client, settings):
    with client.app.state.sessionmaker() as db:
        folder = Folder(name="旅行 / & < >")
        db.add(folder)
        db.flush()
        child = Folder(name="台湾", parent_id=folder.id)
        other = Folder(name="Elsewhere")
        db.add_all([child, other])
        db.flush()
        data = bytes(range(256)) * 4
        settings.library_dir.mkdir(parents=True, exist_ok=True)
        path = settings.library_dir / ("a" * 32 + ".mp4")
        path.write_bytes(data)
        video = Video(
            id="a" * 32,
            title="海边 & <家人>",
            file_path="library/" + path.name,
            status="ready",
            size=len(data),
            folder_id=child.id,
        )
        db.add(video)
        collection = Collection(name="Favorites")
        db.add(collection)
        db.flush()
        db.add(CollectionItem(collection_id=collection.id, video_id=video.id, position=0))
        db.add_all(
            [
                Video(id="b" * 32, title="Processing", file_path="library/no.mp4"),
                Video(
                    id="c" * 32,
                    title="Trashed",
                    file_path="library/no.mp4",
                    status="ready",
                    deleted_at=video.created_at,
                ),
            ]
        )
        db.commit()
        return {
            "folder": folder.id,
            "child": child.id,
            "other": other.id,
            "video": video.id,
            "collection": collection.id,
            "data": data,
            "path": path,
        }


def test_disabled_by_default_and_login_cookie_never_grants_dav_access(client):
    assert client.get("/dav/").status_code == 404
    assert client.get("/api/system/webdav").json() == {
        "enabled": False,
        "credential_configured": False,
        "username": "reelvault",
        "path": "/dav/",
    }
    assert client.put("/api/system/webdav", json={"enabled": True}).status_code == 409
    auth = credential(client)
    for headers in (
        {},
        {"Authorization": "Bearer bad"},
        {"Authorization": "Basic !!!"},
        {"Authorization": "Basic " + base64.b64encode(b"admin:secret123").decode()},
    ):
        response = client.get("/dav/", headers=headers)
        assert response.status_code == 401
        assert response.headers["www-authenticate"].startswith("Basic ")
    assert client.request("OPTIONS", "/dav/", headers=auth).headers["dav"] == "1"
    client.cookies.clear()
    assert client.get("/api/system/webdav").status_code == 401
    assert client.post("/api/system/webdav/credential").status_code == 401
    assert len(listing(client, "/dav/", auth)) == 4


def test_browse_folders_collections_unicode_and_ready_media_only(client, media):
    auth = credential(client)
    rows = listing(client, "/dav/", auth)
    assert [href(row) for row in rows] == [
        "/dav/",
        "/dav/all/",
        "/dav/folders/",
        "/dav/collections/",
    ]
    all_rows = listing(client, "/dav/all/", auth)
    assert len(all_rows) == 2
    assert unquote(href(all_rows[1])).endswith("海边 & <家人>.mp4")
    assert all_rows[1].findtext(f"{DAV}propstat/{DAV}prop/{DAV}getcontentlength") == "1024"
    folder_rows = listing(client, "/dav/folders/", auth)
    top = next(href(row) for row in folder_rows if f"/{media['folder']}--" in href(row))
    children = listing(client, top, auth)
    nested = next(href(row) for row in children if f"/{media['child']}--" in href(row))
    assert len(listing(client, nested, auth)) == 2
    collection = listing(client, "/dav/collections/", auth)[1]
    assert len(listing(client, href(collection), auth)) == 2
    invalid = f"/dav/folders/{media['other']}--other/{media['child']}--wrong/"
    assert client.request("PROPFIND", invalid, headers={**auth, "Depth": "1"}).status_code == 404
    assert (
        client.get(
            f"/dav/folders/{media['other']}--other/{media['video']}--wrong.mp4", headers=auth
        ).status_code
        == 404
    )
    assert (
        client.get(f"/dav/folders/{media['video']}--wrong.mp4/extra/", headers=auth).status_code
        == 404
    )


def test_media_ranges_head_etags_and_rename_stability(client, media):
    auth = credential(client)
    row = listing(client, "/dav/all/", auth)[1]
    path = href(row)
    response = client.get(path, headers=auth)
    assert response.content == media["data"] and response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["etag"] == row.findtext(f"{DAV}propstat/{DAV}prop/{DAV}getetag")
    head = client.head(path, headers=auth)
    assert head.content == b"" and head.headers["content-length"] == "1024"
    partial = client.get(path, headers={**auth, "Range": "bytes=10-19"})
    assert partial.status_code == 206 and partial.content == media["data"][10:20]
    assert partial.headers["content-range"] == "bytes 10-19/1024"
    assert client.get(path, headers={**auth, "Range": "bytes=2048-"}).status_code == 416
    with client.app.state.sessionmaker() as db:
        db.get(Video, media["video"]).title = "Renamed"
        db.commit()
    assert client.get(path, headers=auth).content == media["data"]
    media["path"].unlink()
    assert client.get(path, headers=auth).status_code == 404


@pytest.mark.parametrize(
    "method",
    ["PUT", "DELETE", "POST", "PATCH", "MKCOL", "COPY", "MOVE", "LOCK", "UNLOCK", "PROPPATCH"],
)
def test_all_mutations_are_rejected_and_original_is_unchanged(client, media, method):
    auth = credential(client)
    path = href(listing(client, "/dav/all/", auth)[1])
    response = client.request(method, path, headers=auth, content=b"overwrite")
    assert (
        response.status_code == 405 and response.headers["allow"] == "OPTIONS, PROPFIND, GET, HEAD"
    )
    assert media["path"].read_bytes() == media["data"]
    with client.app.state.sessionmaker() as db:
        assert db.get(Video, media["video"]).deleted_at is None


def test_propfind_selection_unknown_properties_depth_and_bounded_xml(client):
    auth = credential(client)
    body = (
        b'<d:propfind xmlns:d="DAV:" xmlns:x="urn:test"><d:prop>'
        b"<d:displayname/><x:missing/></d:prop></d:propfind>"
    )
    row = listing(client, "/dav/", auth, "0", body)[0]
    assert row.findtext(f"{DAV}propstat/{DAV}prop/{DAV}displayname") == "ReelVault"
    assert [p.findtext(DAV + "status") for p in row.findall(DAV + "propstat")] == [
        "HTTP/1.1 200 OK",
        "HTTP/1.1 404 Not Found",
    ]
    propname = b'<d:propfind xmlns:d="DAV:"><d:propname/></d:propfind>'
    prop = listing(client, "/dav/", auth, "0", propname)[0].find(f"{DAV}propstat/{DAV}prop")
    assert len(prop) == 9 and all(not child.text and not list(child) for child in prop)
    for depth, expected in (("infinity", 403), ("2", 400)):
        assert (
            client.request("PROPFIND", "/dav/", headers={**auth, "Depth": depth}).status_code
            == expected
        )
    for content in (
        b"<bad",
        b"x" * 65537,
        b'<!DOCTYPE x [<!ENTITY test "boom">]><x/>',
        b"<x/>",
        b'<d:propfind xmlns:d="DAV:"><d:prop/><d:allprop/></d:propfind>',
    ):
        assert (
            client.request(
                "PROPFIND", "/dav/", headers={**auth, "Depth": "0"}, content=content
            ).status_code
            == 400
        )


def test_rotate_disable_revoke_and_hash_only_persistence(client, settings):
    old = credential(client)
    new = credential(client)
    assert client.request("PROPFIND", "/dav/", headers={**old, "Depth": "0"}).status_code == 401
    assert len(listing(client, "/dav/", new)) == 4
    with client.app.state.sessionmaker() as db:
        value = db.scalar(select(RuntimeSetting).where(RuntimeSetting.key == "webdav")).value
        secret = base64.b64decode(new["Authorization"].split()[1]).decode().split(":", 1)[1]
        assert secret not in json.dumps(value) and len(value["webdav_token_hash"]) == 64
    assert "webdav_token_hash" not in settings.model_dump()
    assert "webdav_token_hash" not in client.get("/api/system/webdav").json()
    assert client.put("/api/system/webdav", json={"enabled": False}).status_code == 200
    assert client.get("/dav/", headers=new).status_code == 404
    assert client.put("/api/system/webdav", json={"enabled": True}).status_code == 200
    assert len(listing(client, "/dav/", new)) == 4
    assert client.delete("/api/system/webdav/credential").status_code == 200
    assert client.get("/dav/", headers=new).status_code == 404
    assert client.put("/api/system/webdav", json={"enabled": True}).status_code == 409


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_xml_encodings_work_and_dtd_and_entities_are_forbidden(client, encoding):
    auth = credential(client)
    body = '<d:propfind xmlns:d="DAV:"><d:allprop/></d:propfind>'.encode(encoding)
    assert len(listing(client, "/dav/", auth, "0", body)) == 1
    attack = (
        '<!DOCTYPE d:propfind [<!ENTITY secret SYSTEM "file:///etc/passwd">]>'
        '<d:propfind xmlns:d="DAV:"><d:prop>&secret;</d:prop></d:propfind>'
    ).encode(encoding)
    assert (
        client.request(
            "PROPFIND", "/dav/", headers={**auth, "Depth": "0"}, content=attack
        ).status_code
        == 400
    )


def test_disconnected_external_media_can_be_browsed_but_never_uses_wrong_disk(
    client, settings, tmp_path
):
    from reelvault.locations import register_root

    root = tmp_path / "external"
    root.mkdir()
    identifier, location = register_root(settings, root, "External")
    settings.storage_locations[identifier] = location
    original = root / "library/original.mp4"
    original.write_bytes(b"external video")
    with client.app.state.sessionmaker() as db:
        db.add(
            Video(
                id="d" * 32,
                title="External",
                file_path=f"volumes/{identifier}/original.mp4",
                size=original.stat().st_size,
                status="ready",
            )
        )
        db.commit()
    auth = credential(client)
    path = href(listing(client, "/dav/all/", auth)[1])
    assert client.get(path, headers=auth).content == b"external video"
    root.rename(tmp_path / "offline")
    assert len(listing(client, "/dav/all/", auth)) == 2
    assert client.get(path, headers=auth).status_code == 503
    assert not root.exists()
    root.mkdir()
    (root / "library").mkdir()
    (root / "library/original.mp4").write_bytes(b"wrong disk")
    assert client.get(path, headers=auth).status_code == 503


def test_concurrent_mutations_do_not_reactivate_a_stale_in_memory_credential(client, settings):
    from reelvault.auth import hash_token

    old = credential(client)
    current = credential(client)
    old_secret = base64.b64decode(old["Authorization"].split()[1]).decode().split(":", 1)[1]
    settings.webdav_token_hash = hash_token(old_secret)
    assert client.put("/api/system/webdav", json={"enabled": True}).status_code == 200
    assert client.get("/dav/", headers=old).status_code == 401
    assert len(listing(client, "/dav/", current)) == 4


def test_restart_preserves_access_but_restore_revokes_player_credential(settings, tmp_path):
    from fastapi.testclient import TestClient

    from reelvault.backup import create_backup, restore_backup
    from reelvault.config import Settings
    from reelvault.main import create_app

    from .conftest import HEADERS, login

    with TestClient(create_app(settings), headers=HEADERS) as client:
        login(client)
        auth = credential(client)
        archive = create_backup(settings, tmp_path / "backup.zip")
    restarted = Settings(
        data_dir=settings.data_dir, static_dir=tmp_path / "no-static", update_check=False
    )
    with TestClient(create_app(restarted), headers=HEADERS) as client:
        assert len(listing(client, "/dav/", auth)) == 4
    restored = Settings(
        data_dir=tmp_path / "restored", static_dir=tmp_path / "no-static", update_check=False
    )
    restore_backup(archive, restored)
    with TestClient(create_app(restored), headers=HEADERS) as client:
        assert client.get("/dav/", headers=auth).status_code == 404
        login(client)
        assert client.get("/api/system/webdav").json()["credential_configured"] is False


def test_real_webdav_client_download_and_ffprobe_over_http(settings, samples, tmp_path):
    import socket
    import subprocess
    import sys
    import time

    import httpx
    from fastapi.testclient import TestClient
    from webdav4.client import Client

    from reelvault.main import create_app

    from .conftest import HEADERS, login, upload_ready

    with TestClient(create_app(settings), headers=HEADERS) as setup:
        login(setup)
        video = upload_ready(setup, samples["a"])
        auth = credential(setup)
    password = base64.b64decode(auth["Authorization"].split()[1]).decode().split(":", 1)[1]
    with socket.socket() as listener, (tmp_path / "server.log").open("w+") as log:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        env = {
            **os.environ,
            "REELVAULT_DATA_DIR": str(settings.data_dir),
            "REELVAULT_STATIC_DIR": str(tmp_path / "no-static"),
            "REELVAULT_UPDATE_CHECK": "false",
        }
        if os.name == "nt":
            # No descriptor inheritance on Windows: hand over the free port instead.
            port = str(listener.getsockname()[1])
            listener.close()
            serve, inherit = ["--port", port], ()
        else:
            serve, inherit = ["--fd", str(listener.fileno())], (listener.fileno(),)
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "reelvault.main:create_app", "--factory", *serve],
            env=env,
            stdout=log,
            stderr=log,
            pass_fds=inherit,
        )
        try:
            deadline = time.monotonic() + 20
            with httpx.Client(timeout=0.25) as http:
                while True:
                    assert process.poll() is None
                    try:
                        if http.get(origin + "/healthz").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    assert time.monotonic() < deadline
                    time.sleep(0.05)
            remote = Client(origin + "/dav/", auth=("reelvault", password), retry=False)
            assert len(remote.ls("", detail=False)) == 3
            files = remote.ls("all", detail=False)
            assert len(files) == 1 and video["id"] in files[0]
            downloaded = tmp_path / "downloaded.mp4"
            remote.download_file(files[0], str(downloaded))
            assert downloaded.read_bytes() == samples["a"].read_bytes()
            output = subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-headers",
                    "Authorization: " + auth["Authorization"] + "\r\n",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    origin + "/dav/" + files[0],
                ],
                timeout=20,
            )
            assert float(json.loads(output)["format"]["duration"]) >= 3.9
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
