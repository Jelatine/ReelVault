from pathlib import Path

from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text

from reelvault.auth import hash_password
from reelvault.db import make_engine
from reelvault.migrate import alembic_config, upgrade
from reelvault.models import User, Video


def seed_video(client: TestClient, title: str = "测试") -> str:
    with client.app.state.sessionmaker() as db:
        video = Video(title=title, file_path="library/test.mp4", duration=120, status="ready")
        db.add(video)
        db.commit()
        return video.id


def test_rating_favorite_filters_and_sort(client: TestClient) -> None:
    first, second = seed_video(client), seed_video(client, "另一部")
    url = f"/api/videos/{first}"
    assert client.get(url).json()["rating"] == 0
    updated = client.patch(url, json={"rating": 4, "favorite": True}).json()
    assert updated["rating"] == 4 and updated["favorite"] is True
    assert client.get(url).json()["favorite"] is True
    for invalid in (-1, 6, 3.5):
        assert client.patch(url, json={"rating": invalid}).status_code == 422
    assert client.get("/api/videos?rating_min=4&favorite=true").json()["total"] == 1
    assert client.get("/api/videos?rating_min=5").json()["total"] == 0
    assert client.get("/api/videos?favorite=false").json()["items"][0]["id"] == second
    assert client.get("/api/videos?sort=rating").json()["items"][0]["id"] == first
    assert client.get("/api/videos?sort=favorite&order=asc").json()["items"][0]["id"] == second
    assert client.patch(url, json={"rating": 0, "favorite": False}).status_code == 200
    assert client.get("/api/videos?favorite=true").json()["total"] == 0


def test_playback_count_resume_and_user_isolation(client: TestClient) -> None:
    video_id = seed_video(client)
    url = f"/api/videos/{video_id}/playback"
    assert client.get(url).json() == {"position": 0, "play_count": 0, "last_played_at": None}
    assert client.post(url + "/start").json()["play_count"] == 1
    saved = client.put(url, json={"position": 37}).json()
    assert saved["position"] == 37 and saved["play_count"] == 1
    assert saved["last_played_at"]
    assert client.get(url).json()["position"] == 37
    assert client.get("/api/playback").json()[0]["video_id"] == video_id
    assert client.post(url + "/start").json()["position"] == 37
    assert client.get(url).json()["play_count"] == 2
    assert client.put(url, json={"position": 999}).json()["position"] == 120
    assert client.put(url, json={"position": -1}).status_code == 422
    assert (
        client.put(
            url, content='{"position": NaN}', headers={"Content-Type": "application/json"}
        ).status_code
        == 422
    )
    assert client.put(url, json={"position": 0}).json()["position"] == 0

    with client.app.state.sessionmaker() as db:
        db.add(User(username="second", password_hash=hash_password("secret123")))
        db.commit()
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "second", "password": "secret123"})
    assert client.get(url).json()["play_count"] == 0
    assert client.get("/api/playback").json() == []
    assert client.post(url + "/start").json()["play_count"] == 1
    client.delete(f"/api/videos/{video_id}")
    assert client.get("/api/playback").json() == []
    assert client.put(url, json={"position": 20}).status_code == 404
    client.delete(f"/api/videos/{video_id}?permanent=true")
    with client.app.state.sessionmaker() as db:
        assert db.scalar(text("SELECT count(*) FROM playback")) == 0


def test_upgrade_existing_library_preserves_video(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "old.db")
    cfg = alembic_config(str(engine.url))
    command.upgrade(cfg, "0001")
    with engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO videos (id, title, description, original_name, file_path,
              status, size, duration, width, height, fps, bitrate, container, video_codec,
              meta, has_poster, has_preview, has_sprite, asset_version, created_at, updated_at)
            VALUES ('old', '原视频', '', '', 'library/old.mp4', 'ready', 10, 1, 10, 10,
              25, 1, 'mp4', 'h264', '{}', 0, 0, 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        )
        conn.execute(text("INSERT INTO tags (id, name) VALUES (1, '旧库标签')"))
        conn.execute(text("INSERT INTO video_tags (video_id, tag_id) VALUES ('old', 1)"))
    upgrade(engine)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT title, rating, favorite FROM videos WHERE id='old'")).one()
        assert tuple(row) == ("原视频", 0, 0)
        assert conn.scalar(text("SELECT count(*) FROM playback")) == 0
        assert (
            conn.scalar(
                text("SELECT count(*) FROM video_search WHERE title='原视频' AND tags='旧库标签'")
            )
            == 1
        )
    engine.dispose()
