from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from sqlalchemy import select
from sqlalchemy.orm import Session

from reelvault.auth import hash_password
from reelvault.db import make_engine
from reelvault.migrate import alembic_config, upgrade
from reelvault.models import Bookmark, User, Video

from .conftest import upload_ready, wait_job
from .test_scenes import scene_video


def test_bookmark_crud_order_bounds_and_owner(client, settings, samples):
    video = upload_ready(client, samples["a"])
    url = f"/api/videos/{video['id']}/bookmarks"
    assert client.get(url).json() == {"bookmarks": [], "chapters": [], "chapters_stale": False}
    later = client.post(
        url, json={"position": 2.75, "title": "Later", "note": "逐帧检查\n中文备注"}
    ).json()
    earlier = client.post(
        url, json={"position": 0.123, "title": "  Start  ", "kind": "chapter"}
    ).json()
    end = client.post(url, json={"position": video["duration"], "title": "结尾"}).json()
    assert [m["id"] for m in client.get(url).json()["bookmarks"]] == [
        earlier["id"],
        later["id"],
        end["id"],
    ]
    assert earlier["title"] == "Start" and not earlier["stale"]
    chapters = client.get(url).json()["chapters"]
    assert chapters[0] == {"start": 0, "end": 0.123, "title": "开头"}
    assert chapters[-1] == {"start": 0.123, "end": video["duration"], "title": "Start"}
    updated = client.put(
        url + "/" + later["id"], json={"position": 1.001, "title": "New", "note": "新备注"}
    ).json()
    assert updated["position"] == 1.001 and updated["note"] == "新备注"
    for body in [
        {"position": -1},
        {"position": video["duration"] + 0.01},
        {"position": video["duration"], "kind": "chapter"},
        {"position": 0, "title": "x" * 129},
        {"position": 0, "note": "x" * 2001},
        {"position": 0, "kind": "wrong"},
    ]:
        assert client.post(url, json=body).status_code == 422
    assert (
        client.post(
            url, content='{"position":NaN}', headers={"Content-Type": "application/json"}
        ).status_code
        == 422
    )
    assert client.delete(url + "/" + end["id"]).status_code == 200
    assert client.delete(url + "/" + end["id"]).status_code == 404
    with client.app.state.sessionmaker() as db:
        other = User(username="other", password_hash=hash_password("secret123"))
        db.add(other)
        db.commit()
    client.cookies.clear()
    assert (
        client.post(
            "/api/auth/login", json={"username": "other", "password": "secret123"}
        ).status_code
        == 200
    )
    assert client.get(url).json()["bookmarks"] == []
    assert client.put(url + "/" + later["id"], json={"position": 0}).status_code == 404
    assert client.delete(url + "/" + later["id"]).status_code == 404
    client.cookies.clear()
    assert client.get(url).status_code == 401
    assert client.post(url, json={"position": 0}).status_code == 401


def test_scene_chapters_manual_override_stale_and_rebase(client, settings, tmp_path):
    video = upload_ready(client, scene_video(tmp_path / "scenes.mp4"))
    url = f"/api/videos/{video['id']}/bookmarks"
    detected = client.post(f"/api/videos/{video['id']}/scenes", json={"min_interval": 0.1}).json()
    assert wait_job(client, detected["id"])["status"] == "succeeded"
    assert len(client.get(url).json()["chapters"]) == 4
    manual = client.post(url, json={"position": 1, "title": "第二幕", "kind": "chapter"}).json()
    assert client.get(url).json()["chapters"][1]["title"] == "第二幕"
    with client.app.state.sessionmaker() as db:
        source = settings.data_dir / db.get(Video, video["id"]).file_path
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    result = client.get(url).json()
    assert result["bookmarks"][0]["stale"] and result["chapters"] == [] and result["chapters_stale"]
    assert (
        client.put(
            url + "/" + manual["id"], json={"position": 1.2, "title": "校正章节", "kind": "chapter"}
        ).status_code
        == 200
    )
    result = client.get(url).json()
    assert not result["bookmarks"][0]["stale"]
    assert result["chapters"][-1]["start"] == 1.2
    # Poster changes don't alter bookmark signatures.
    with client.app.state.sessionmaker() as db:
        v = db.get(Video, video["id"])
        v.asset_version += 1
        db.commit()
    assert not client.get(url).json()["bookmarks"][0]["stale"]
    client.delete(f"/api/videos/{video['id']}")
    assert client.get(url).status_code == 404
    client.post(f"/api/videos/{video['id']}/restore")
    assert len(client.get(url).json()["bookmarks"]) == 1
    client.delete(f"/api/videos/{video['id']}?permanent=true")
    with client.app.state.sessionmaker() as db:
        assert list(db.scalars(select(Bookmark).where(Bookmark.video_id == video["id"]))) == []


def test_bookmark_wrong_video_and_unready(client, samples):
    a = upload_ready(client, samples["a"])
    b = upload_ready(client, samples["b"])
    mark = client.post(f"/api/videos/{a['id']}/bookmarks", json={"position": 1}).json()
    assert client.delete(f"/api/videos/{b['id']}/bookmarks/{mark['id']}").status_code == 404
    with client.app.state.sessionmaker() as db:
        v = db.get(Video, a["id"])
        v.status = "processing"
        db.commit()
    assert client.post(f"/api/videos/{a['id']}/bookmarks", json={"position": 0}).status_code == 409


def test_bookmark_migration_upgrade_and_downgrade(tmp_path: Path):
    engine = make_engine(tmp_path / "old.db")
    cfg = alembic_config(str(engine.url))
    command.upgrade(cfg, "0013")
    # Historical schemas must not be read through today's full ORM mapping.
    with engine.begin() as db:
        uid = db.execute(
            User.__table__.insert().values(username="old", password_hash="hash")
        ).inserted_primary_key[0]
        vid = db.execute(
            Video.__table__.insert().values(title="old", file_path="library/old.mp4")
        ).inserted_primary_key[0]
    upgrade(engine)
    with Session(engine) as db:
        db.add(
            Bookmark(
                user_id=uid,
                video_id=vid,
                position=0.1,
                title="test",
                note="备注",
                kind="bookmark",
                signature=[],
            )
        )
        db.commit()
        assert db.scalar(select(Bookmark)).note == "备注"
    command.downgrade(cfg, "0013")
    with engine.connect() as db:
        assert (
            db.scalar(select(Video.__table__.c.title).where(Video.__table__.c.id == vid)) == "old"
        )
        assert (
            db.scalar(select(User.__table__.c.username).where(User.__table__.c.id == uid)) == "old"
        )
    engine.dispose()
