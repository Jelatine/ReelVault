from __future__ import annotations

from reelvault.models import Video

from .conftest import make_video, upload_ready, wait_job

URL = "/api/system/media-probe"
DETAILS = ("video_profile", "video_level", "video_bitrate", "audio_bitrate", "channel_layout")


def _strip(client, video_id: str) -> None:
    """Make the row look like it was ingested before stream details were probed."""
    with client.app.state.sessionmaker() as db:
        row = db.get(Video, video_id)
        row.meta = {
            **{k: v for k, v in row.meta.items() if k not in DETAILS},
            "sprite_version": 3,
        }
        db.commit()


def test_backfill_probes_only_legacy_videos(client, tmp_path):
    legacy = upload_ready(client, make_video(tmp_path / "a.mp4", duration=1))
    current = upload_ready(client, make_video(tmp_path / "b.mp4", duration=1))
    _strip(client, legacy["id"])
    before = client.get(f"/api/videos/{legacy['id']}").json()
    assert "video_profile" not in before["media"] and before["media"]["pix_fmt"] == "yuv420p"
    assert client.get(URL).json() == {"missing": 1, "active": 0}

    assert client.post(URL).json() == {"submitted": 1, "skipped": 0}
    jobs = [j for j in client.get("/api/jobs").json() if j["kind"] == "probe"]
    assert [j["video_ids"] for j in jobs] == [[legacy["id"]]] and jobs[0]["priority"] == 0
    assert wait_job(client, jobs[0]["id"])["status"] == "succeeded"

    after = client.get(f"/api/videos/{legacy['id']}").json()
    assert after["media"].keys() == current["media"].keys()
    assert after["media"]["video_profile"] and after["media"]["audio_bitrate"] > 0
    # Other metadata and cache-busting state survive the merge.
    assert after["thumbnails_url"].endswith("&s=3") and after["stream_url"] == legacy["stream_url"]
    assert client.get(URL).json() == {"missing": 0, "active": 0}
    assert client.post(URL).json() == {"submitted": 0, "skipped": 0}


def test_backfill_ignores_deleted_videos(client, tmp_path):
    video = upload_ready(client, make_video(tmp_path / "a.mp4", duration=1, audio=False))
    _strip(client, video["id"])
    client.post("/api/videos/batch", json={"ids": [video["id"]], "action": "delete"})
    assert client.get(URL).json()["missing"] == 0
    assert client.post(URL).json()["submitted"] == 0
