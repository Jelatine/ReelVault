from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from .conftest import upload, upload_ready, wait_ready


def test_upload_probe_and_derived_assets(client: TestClient, samples: dict[str, Path]) -> None:
    v = upload_ready(client, samples["a"])
    assert v["title"] == "clip_a"
    assert v["width"] == 320 and v["height"] == 240
    assert 3.5 < v["duration"] < 4.5
    assert v["video_codec"] == "h264" and v["audio_codec"] == "aac"
    for key in ("poster_url", "preview_url", "thumbnails_url"):
        r = client.get(v[key])
        assert r.status_code == 200 and len(r.content) > 0, key
    vtt = client.get(v["thumbnails_url"]).text
    assert vtt.startswith("WEBVTT") and "#xywh=" in vtt
    sprite_ref = vtt.split("\n")[3].split("#")[0]
    assert sprite_ref.startswith(f"/api/videos/{v['id']}/sprite.jpg")
    assert client.get(sprite_ref).status_code == 200


def test_stream_supports_range(client: TestClient, samples: dict[str, Path]) -> None:
    v = upload_ready(client, samples["a"])
    full = client.get(v["stream_url"])
    assert full.status_code == 200
    assert full.headers["accept-ranges"] == "bytes"
    r = client.get(v["stream_url"], headers={"Range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.content == full.content[100:200]
    assert r.headers["content-range"].startswith("bytes 100-199/")


def test_mkv_gets_playable_copy(client: TestClient, samples: dict[str, Path]) -> None:
    v = upload_ready(client, samples["mkv"])
    r = client.get(v["stream_url"])
    assert r.headers["content-type"] == "video/mp4"


def test_resumable_upload_rejects_wrong_offset(
    client: TestClient, samples: dict[str, Path]
) -> None:
    data = samples["b"].read_bytes()
    up = client.post("/api/uploads", json={"filename": "b.mp4", "size": len(data)}).json()
    client.put(f"/api/uploads/{up['id']}?offset=0", content=data[:1000])
    r = client.put(f"/api/uploads/{up['id']}?offset=0", content=data[:1000])
    assert r.status_code == 200  # re-sending from a known offset is allowed
    r = client.put(f"/api/uploads/{up['id']}?offset=5000", content=data[5000:6000])
    assert r.status_code == 409
    assert client.get(f"/api/uploads/{up['id']}").json()["received"] == 1000
    assert client.post(f"/api/uploads/{up['id']}/complete").status_code == 400
    client.put(f"/api/uploads/{up['id']}?offset=1000", content=data[1000:])
    v = client.post(f"/api/uploads/{up['id']}/complete").json()
    wait_ready(client, v["id"])


def test_rejects_non_video(client: TestClient) -> None:
    r = client.post("/api/uploads", json={"filename": "notes.txt", "size": 10})
    assert r.status_code == 400


def test_folders_tags_search_trash(client: TestClient, samples: dict[str, Path]) -> None:
    parent = client.post("/api/folders", json={"name": "旅行"}).json()
    child = client.post("/api/folders", json={"name": "2024", "parent_id": parent["id"]}).json()
    assert client.post("/api/folders", json={"name": "旅行"}).status_code == 409
    r = client.patch(f"/api/folders/{parent['id']}", json={"move": True, "parent_id": child["id"]})
    assert r.status_code == 400

    a = upload(client, samples["a"], folder_id=child["id"])
    b = upload(client, samples["b"])
    assert a["folder_id"] == child["id"]

    r = client.patch(f"/api/videos/{b['id']}", json={"title": "海边日落", "tags": ["海", "日落"]})
    assert r.json()["tags"] == ["日落", "海"]
    assert client.get("/api/videos", params={"q": "日落"}).json()["total"] == 1
    assert client.get("/api/videos", params={"tag": "海"}).json()["total"] == 1
    assert client.get("/api/videos", params={"folder": child["id"]}).json()["total"] == 1
    assert client.get("/api/videos", params={"folder": "root"}).json()["total"] == 1
    assert {t["name"] for t in client.get("/api/tags").json()} == {"海", "日落"}

    folders = {f["name"]: f for f in client.get("/api/folders").json()}
    assert folders["2024"]["count"] == 1

    # deleting a folder moves its content up
    client.delete(f"/api/folders/{child['id']}")
    assert client.get(f"/api/videos/{a['id']}").json()["folder_id"] == parent["id"]

    r = client.post("/api/videos/batch", json={"ids": [a["id"], b["id"]], "action": "delete"})
    assert r.json()["updated"] == 2
    assert client.get("/api/videos").json()["total"] == 0
    assert client.get("/api/videos", params={"trash": True}).json()["total"] == 2
    client.post(f"/api/videos/{a['id']}/restore")
    assert client.get("/api/videos").json()["total"] == 1
    for vid in (a["id"], b["id"]):
        wait_ready(client, vid)
    assert client.post("/api/trash/empty").json() == {"deleted": 1}
    assert client.get(f"/api/videos/{b['id']}").status_code == 404


def test_cover_from_time_and_frame(client: TestClient, samples: dict[str, Path]) -> None:
    v = upload_ready(client, samples["a"])
    r = client.post(f"/api/videos/{v['id']}/cover", json={"time": 2.5})
    assert r.status_code == 200
    v2 = r.json()
    assert v2["cover_time"] == 2.5 and v2["poster_url"] != v["poster_url"]
    r = client.get(f"/api/videos/{v['id']}/frame", params={"t": 1})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


def test_system_info(client: TestClient) -> None:
    info = client.get("/api/system/info").json()
    assert info["ffmpeg_version"] != "unavailable"
    assert client.get("/healthz").json()["status"] == "ok"
