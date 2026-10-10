from __future__ import annotations

from reelvault.media.derive import SPRITE, VTT

from .conftest import make_video, upload_ready, wait_job


def _jobs(client, kind: str = "sprite") -> list[dict]:
    return [j for j in client.get("/api/jobs").json() if j["kind"] == kind]


def test_regenerate_single_sprite_refreshes_urls_without_bumping_asset_version(
    client, settings, tmp_path
):
    video = upload_ready(client, make_video(tmp_path / "a.mp4", duration=2))
    vid = video["id"]
    folder = settings.derived_dir / vid
    (folder / SPRITE).write_bytes(b"stale")

    job = client.post(f"/api/videos/{vid}/sprite").json()
    assert job["kind"] == "sprite"
    assert wait_job(client, job["id"])["status"] == "succeeded"

    after = client.get(f"/api/videos/{vid}").json()
    assert after["stream_url"] == video["stream_url"]
    assert after["thumbnails_url"] != video["thumbnails_url"]
    assert after["thumbnails_url"].endswith("&s=1")
    assert (folder / SPRITE).read_bytes()[:2] == b"\xff\xd8"
    vtt = client.get(after["thumbnails_url"]).text
    assert f"/api/videos/{vid}/{SPRITE}?v=" in vtt and "&s=1#xywh=" in vtt
    assert (folder / VTT).read_text() == vtt
    assert not list(settings.tmp_dir.glob("job-*"))

    second = client.post(f"/api/videos/{vid}/sprite").json()
    assert wait_job(client, second["id"])["status"] == "succeeded"
    assert client.get(f"/api/videos/{vid}").json()["thumbnails_url"].endswith("&s=2")


def test_single_sprite_rejects_missing_and_deleted_videos(client, tmp_path):
    assert client.post("/api/videos/" + "f" * 32 + "/sprite").json()["code"] == "video_not_found"
    video = upload_ready(client, make_video(tmp_path / "a.mp4", duration=1, audio=False))
    client.post("/api/videos/batch", json={"ids": [video["id"]], "action": "delete"})
    response = client.post(f"/api/videos/{video['id']}/sprite")
    assert response.status_code == 404


def test_batch_regenerates_selected_or_all_ready_videos(client, tmp_path):
    a = upload_ready(client, make_video(tmp_path / "a.mp4", duration=1, audio=False))
    b = upload_ready(client, make_video(tmp_path / "b.mp4", duration=1, audio=False))

    result = client.post("/api/videos/sprites", json={"video_ids": [a["id"], "f" * 32]})
    assert result.json() == {"submitted": 1, "skipped": 1}
    for job in _jobs(client):
        assert job["priority"] == 0
        assert wait_job(client, job["id"])["status"] == "succeeded"

    result = client.post("/api/videos/sprites", json={}).json()
    assert result == {"submitted": 2, "skipped": 0}
    for job in _jobs(client):
        assert wait_job(client, job["id"])["status"] == "succeeded"
    for vid, version in ((a["id"], 2), (b["id"], 1)):
        url = client.get(f"/api/videos/{vid}").json()["thumbnails_url"]
        assert url.endswith(f"&s={version}")

    assert client.post("/api/videos/sprites", json={"video_ids": []}).status_code == 422
