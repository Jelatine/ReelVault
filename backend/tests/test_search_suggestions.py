from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from reelvault.auth import hash_password
from reelvault.db import make_engine
from reelvault.migrate import alembic_config
from reelvault.models import RecentSearch, User, Video

from .test_organization import seed_video


def test_history_migration_round_trip_preserves_users_videos_and_pinyin(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "history.db")
    cfg = alembic_config(str(engine.url))
    command.upgrade(cfg, "0023")
    with Session(engine) as db:
        user = User(username="legacy", password_hash=hash_password("secret123"))
        db.add_all(
            [user, Video(id="legacy", title="旅行日落", file_path="library/a.mp4", status="ready")]
        )
        db.commit()
        user_id = user.id
    command.upgrade(cfg, "0024")
    with Session(engine) as db:
        db.add(RecentSearch(user_id=user_id, query="lxrl"))
        db.commit()
        assert db.scalar(select(RecentSearch.query)) == "lxrl"
    command.downgrade(cfg, "0023")
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT username FROM users")) == "legacy"
        assert conn.scalar(text("SELECT title_initials FROM video_search")) == "lxrl"
        assert (
            conn.scalar(text("SELECT count(*) FROM video_search WHERE video_search MATCH 'lxrl'"))
            == 1
        )
    command.upgrade(cfg, "0024")
    with Session(engine) as db:
        assert db.scalar(select(RecentSearch)) is None
        db.add(RecentSearch(user_id=user_id, query="旅行"))
        db.commit()
        db.delete(db.get(User, user_id))
        db.commit()
        assert db.scalar(select(RecentSearch)) is None
        assert db.get(Video, "legacy") is not None
    engine.dispose()


def test_suggestions_title_pinyin_tags_folders_literal_wildcards_and_active_only(
    client: TestClient,
) -> None:
    video = seed_video(client, "旅行日落")
    gone = seed_video(client, "旅行删除")
    client.delete(f"/api/videos/{gone}")
    client.patch(f"/api/videos/{video}", json={"tags": ["旅行", "100%_"]})
    folder = client.post("/api/folders", json={"name": "旅行目录"}).json()
    response = client.get("/api/search/suggestions", params={"q": "旅行"})
    assert response.status_code == 200
    assert response.json()["videos"] == [{"id": video, "name": "旅行日落"}]
    assert response.json()["tags"][0]["name"] == "旅行"
    assert response.json()["folders"] == [
        {"id": folder["id"], "name": "旅行目录", "parent_id": None}
    ]
    assert (
        client.get("/api/search/suggestions", params={"q": "LXRL"}).json()["videos"][0]["id"]
        == video
    )
    assert (
        client.get("/api/search/suggestions", params={"q": "lǚxíng"}).json()["videos"][0]["id"]
        == video
    )
    for query in ("%", "_", "100%_"):
        data = client.get("/api/search/suggestions", params={"q": query}).json()
        assert data["videos"] == [] and data["folders"] == []
        assert [t["name"] for t in data["tags"]] == ["100%_"]
    assert client.get("/api/search/suggestions", params={"q": ""}).json() == {
        "videos": [],
        "tags": [],
        "folders": [],
    }
    for query in ('"', "OR", "NEAR(foo bar)", "rating:>=", "a' OR 1=1 --"):
        assert client.get("/api/search/suggestions", params={"q": query}).status_code == 200
    client.patch(f"/api/videos/{video}", json={"title": "重庆音乐"})
    assert client.get("/api/search/suggestions", params={"q": "lxrl"}).json()["videos"] == []
    assert (
        client.get("/api/search/suggestions", params={"q": "cqyy"}).json()["videos"][0]["id"]
        == video
    )


def test_recent_dedup_trim_limit_order_validation_and_concurrent_writes(client: TestClient) -> None:
    for i in range(22):
        assert client.post("/api/search/recent", json={"query": f"检索{i}"}).status_code == 200
    rows = client.get("/api/search/recent").json()
    assert len(rows) == 20 and rows[0]["query"] == "检索21" and rows[-1]["query"] == "检索2"
    first = client.post("/api/search/recent", json={"query": " 检索2 "}).json()
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(lambda _: client.post("/api/search/recent", json={"query": "检索2"}), range(4))
        )
    assert all(r.status_code == 200 for r in responses)
    rows = client.get("/api/search/recent").json()
    assert rows[0]["id"] == first["id"] and len(rows) == 20
    assert sum(r["query"] == "检索2" for r in rows) == 1
    assert client.post("/api/search/recent", json={"query": "rating:6"}).status_code == 400
    assert client.post("/api/search/recent", json={"query": " "}).status_code == 400
    assert client.post("/api/search/recent", json={"query": "a" * 513}).status_code == 422
    assert client.delete(f"/api/search/recent/{first['id']}").status_code == 200
    assert len(client.get("/api/search/recent").json()) == 19
    assert client.delete("/api/search/recent").status_code == 200
    assert client.get("/api/search/recent").json() == []
    fresh = client.post("/api/search/recent", json={"query": "新搜索"}).json()
    assert fresh["id"] > max(row["id"] for row in rows)
    assert client.delete(f"/api/search/recent/{first['id']}").status_code == 404
    assert client.get("/api/search/recent").json()[0]["id"] == fresh["id"]


def test_recent_accounts_isolated_and_endpoints_require_auth(client: TestClient) -> None:
    first = client.post("/api/search/recent", json={"query": "旅行"}).json()
    with client.app.state.sessionmaker() as db:
        db.add(User(username="search-second", password_hash=hash_password("secret123")))
        db.commit()
    client.post("/api/auth/logout")
    for path in ("/api/search/recent", "/api/search/suggestions?q=旅行"):
        assert client.get(path).status_code == 401
    assert client.post("/api/search/recent", json={"query": "x"}).status_code == 401
    assert client.delete("/api/search/recent").status_code == 401
    assert (
        client.post(
            "/api/auth/login", json={"username": "search-second", "password": "secret123"}
        ).status_code
        == 200
    )
    assert client.get("/api/search/recent").json() == []
    assert client.delete(f"/api/search/recent/{first['id']}").status_code == 404
    assert client.post("/api/search/recent", json={"query": "家庭"}).status_code == 200
    assert client.delete("/api/search/recent").status_code == 200
    with client.app.state.sessionmaker() as db:
        assert [r.query for r in db.scalars(select(RecentSearch))] == ["旅行"]
