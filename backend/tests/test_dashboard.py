from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from reelvault.auth import hash_password
from reelvault.db import make_engine
from reelvault.migrate import alembic_config, upgrade
from reelvault.models import HlsPackage, Playback, User, Video

from .conftest import USER, upload_ready, wait_ready
from .test_edit import result_video, run_edit


def test_sections_order_resume_bounds_storage_and_user_isolation(client):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    with client.app.state.sessionmaker() as db:
        admin = db.scalar(select(User).where(User.username == USER))
        other = User(username="dashboard-other", password_hash=hash_password("secret123"))
        db.add(other)
        db.flush()
        for i in range(8):
            v = Video(
                id=f"{i:032x}",
                title=f"v{i}",
                file_path=f"library/{i}.mp4",
                size=100 + i,
                duration=20,
                status="processing" if i == 5 else "ready",
                favorite=i in (1, 4, 7),
                created_at=start + timedelta(days=i),
                deleted_at=start if i == 7 else None,
                edited_at=start + timedelta(days=10 - i) if i in (0, 2, 7) else None,
            )
            db.add(v)
            db.flush()
            db.add(
                Playback(
                    user_id=admin.id,
                    video_id=v.id,
                    position=[5, 0, 18, 19.2, 20, 3, 1, 5][i],
                    last_played_at=start + timedelta(days=i),
                )
            )
        db.add(Playback(user_id=other.id, video_id=f"{4:032x}", position=6, last_played_at=start))
        db.add(
            HlsPackage(
                video_id=f"{0:032x}", generation="a" * 32, signature={}, renditions=[], size=321
            )
        )
        db.commit()
    response = client.get("/api/dashboard?limit=2")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    data = response.json()
    assert [v["title"] for v in data["continue_watching"]] == ["v2", "v0"]
    assert data["continue_watching"][0]["position"] == 18
    assert [v["title"] for v in data["recent_added"]] == ["v6", "v5"]
    assert [v["title"] for v in data["recent_edited"]] == ["v0", "v2"]
    assert [v["title"] for v in data["favorites"]] == ["v4", "v1"]
    assert data["storage"]["library"] == {"count": 7, "size": sum(range(100, 107))}
    assert data["storage"]["trash"] == {"count": 1, "size": 107}
    assert data["storage"]["hls_size"] == 321
    assert data["storage"]["disk"]["free"] > 0
    for limit in (0, 25, -1):
        assert client.get(f"/api/dashboard?limit={limit}").status_code == 422
    client.cookies.clear()
    client.post("/api/auth/login", json={"username": "dashboard-other", "password": "secret123"})
    assert [v["title"] for v in client.get("/api/dashboard").json()["continue_watching"]] == ["v4"]
    client.cookies.clear()
    assert client.get("/api/dashboard").status_code == 401


def test_empty_dashboard_and_actual_edit_time_survives_metadata_and_job_cleanup(client, samples):
    assert client.get("/api/dashboard").json()["continue_watching"] == []
    source = upload_ready(client, samples["a"])
    assert source["edited_at"] is None
    first = result_video(client, run_edit(client, source["id"], {"op": "mute"}))
    assert first["edited_at"] is not None
    client.patch(f"/api/videos/{first['id']}", json={"title": "renamed", "rating": 4})
    assert client.get(f"/api/videos/{first['id']}").json()["edited_at"] == first["edited_at"]
    run_edit(client, first["id"], {"op": "rotate", "angle": 90}, {"mode": "replace"})
    replaced = wait_ready(client, first["id"])
    assert replaced["edited_at"] > first["edited_at"]
    with client.app.state.sessionmaker() as db:
        old = db.scalar(select(Video).where(Video.deleted_at.is_not(None)))
        assert old.edited_at.isoformat() == first["edited_at"]
    client.delete("/api/jobs")
    assert client.get("/api/dashboard").json()["recent_edited"][0]["id"] == first["id"]


def test_edited_timestamp_migration_backfill_preserves_old_video(tmp_path):
    from alembic import command

    engine = make_engine(tmp_path / "old.db")
    cfg = alembic_config(str(engine.url))
    command.upgrade(cfg, "0015")
    with engine.begin() as db:
        db.execute(
            text("""INSERT INTO videos(id,title,description,original_name,file_path,status,
                size,duration,width,height,fps,bitrate,container,video_codec,
                has_poster,has_preview,has_sprite,asset_version,meta,rating,favorite,
                edit_sources,edit_params,created_at,updated_at)
                VALUES (:id,'old','','','library/old.mp4','ready',12,4,320,240,25,1000,
                'mp4','h264',0,0,0,1,'{}',0,0,'[]',:edit,'2025-01-01','2025-02-01')"""),
            {"id": "a" * 32, "edit": '{"op":"mute"}'},
        )
    upgrade(engine)
    with engine.connect() as db:
        assert db.execute(text("SELECT edited_at FROM videos")).scalar() == "2025-02-01"
    command.downgrade(cfg, "0015")
    with engine.connect() as db:
        assert db.execute(text("SELECT title FROM videos")).scalar() == "old"
    engine.dispose()
