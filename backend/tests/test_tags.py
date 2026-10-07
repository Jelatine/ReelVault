from concurrent.futures import ThreadPoolExecutor

from alembic import command
from sqlalchemy import text

from reelvault.db import make_engine
from reelvault.migrate import alembic_config

from .conftest import upload, wait_ready


def test_tag_rename_color_groups_preserve_videos_and_search(client, samples):
    video = wait_ready(client, upload(client, samples["a"])["id"])
    client.patch(f"/api/videos/{video['id']}", json={"tags": ["旧旅行标签"]})
    tag = client.get("/api/tags").json()[0]
    group = client.post("/api/tags/groups", json={"name": " 地点 "}).json()
    result = client.patch(
        f"/api/tags/{tag['id']}", json={"color": "#ABCDEF", "group_id": group["id"]}
    )
    assert result.status_code == 200, result.text
    assert result.json()["name"] == "旧旅行标签"
    assert result.json()["color"] == "#abcdef"
    renamed = client.patch(f"/api/tags/{tag['id']}", json={"name": "新旅行标签"}).json()
    assert renamed["group_name"] == "地点" and renamed["color"] == "#abcdef"
    assert client.get(f"/api/videos/{video['id']}").json()["tags"] == ["新旅行标签"]
    assert client.get("/api/videos?q=旧旅行标签").json()["total"] == 0
    assert client.get("/api/videos?q=新旅行标签").json()["total"] == 1
    assert (
        client.patch(f"/api/tags/groups/{group['id']}", json={"name": "拍摄地点"}).status_code
        == 200
    )
    assert client.get("/api/tags").json()[0]["group_name"] == "拍摄地点"
    assert client.delete(f"/api/tags/groups/{group['id']}").status_code == 200
    assert client.get("/api/tags").json()[0]["group_id"] is None
    assert client.get(f"/api/videos/{video['id']}").json()["tags"] == ["新旅行标签"]


def test_merge_deduplicates_active_and_trashed_associations_and_delete_keeps_files(client, samples):
    ids = [wait_ready(client, upload(client, samples["a"])["id"])["id"] for _ in range(3)]
    for vid, names in zip(ids, (["来源标签", "目标标签"], ["来源标签"], ["来源标签"]), strict=True):
        client.patch(f"/api/videos/{vid}", json={"tags": names})
    client.delete(f"/api/videos/{ids[2]}")
    tags = {t["name"]: t for t in client.get("/api/tags").json()}
    source, target = tags["来源标签"], tags["目标标签"]
    assert source["count"] == 2 and source["trash_count"] == 1
    result = client.post(f"/api/tags/{source['id']}/merge", json={"target_id": target["id"]})
    assert result.status_code == 200, result.text
    assert result.json()["updated"] == 3
    assert result.json()["tag"]["count"] == 2 and result.json()["tag"]["trash_count"] == 1
    assert all(client.get(f"/api/videos/{vid}").json()["tags"] == ["目标标签"] for vid in ids)
    assert client.get("/api/videos?q=来源标签").json()["total"] == 0
    assert client.get("/api/videos?q=目标标签").json()["total"] == 2
    assert client.delete(f"/api/tags/{target['id']}").status_code == 200
    assert all(client.get(f"/api/videos/{vid}").json()["tags"] == [] for vid in ids)
    assert client.get(f"/api/videos/{ids[0]}/download").status_code == 200
    assert client.get("/api/videos?q=目标标签").json()["total"] == 0


def test_pending_uploads_follow_rename_merge_and_delete(client, samples):
    first = client.post("/api/tags", json={"name": "old"}).json()
    target = client.post("/api/tags", json={"name": "target"}).json()
    data = samples["a"].read_bytes()
    pending = client.post(
        "/api/uploads", json={"filename": "a.mp4", "size": len(data), "tags": ["old", "target"]}
    ).json()
    assert client.patch(f"/api/tags/{first['id']}", json={"name": "renamed"}).status_code == 200
    assert client.get(f"/api/uploads/{pending['id']}").json()["tags"] == ["renamed", "target"]
    client.post(f"/api/tags/{first['id']}/merge", json={"target_id": target["id"]})
    assert client.get(f"/api/uploads/{pending['id']}").json()["tags"] == ["target"]
    client.delete(f"/api/tags/{target['id']}")
    assert client.put(f"/api/uploads/{pending['id']}?offset=0", content=data).status_code == 200
    video = client.post(f"/api/uploads/{pending['id']}/complete").json()
    assert wait_ready(client, video["id"])["tags"] == []
    assert client.get("/api/tags").json() == []


def test_tag_validation_conflicts_and_rollback(client):
    first = client.post("/api/tags", json={"name": "a", "color": "#123456"}).json()
    other = client.post("/api/tags", json={"name": "b"}).json()
    group = client.post("/api/tags/groups", json={"name": "group"}).json()
    pending = client.post(
        "/api/uploads", json={"filename": "a.mp4", "size": 1, "tags": ["a"]}
    ).json()
    result = client.patch(f"/api/tags/{first['id']}", json={"name": "b", "group_id": group["id"]})
    assert result.status_code == 409 and result.json()["code"] == "tag_name_conflict"
    assert client.get(f"/api/uploads/{pending['id']}").json()["tags"] == ["a"]
    assert client.post("/api/tags/groups", json={"name": "group"}).status_code == 409
    for patch in (
        {"color": "red"},
        {"name": "  "},
        {"name": None},
        {"name": "x" * 65},
        {"group_id": 0},
    ):
        assert client.patch(f"/api/tags/{first['id']}", json=patch).status_code == 422
    assert client.patch(f"/api/tags/{first['id']}", json={"group_id": 999}).status_code == 404
    assert (
        client.post(f"/api/tags/{first['id']}/merge", json={"target_id": first["id"]}).status_code
        == 400
    )
    assert client.post(f"/api/tags/{first['id']}/merge", json={"target_id": 999}).status_code == 404
    assert client.delete("/api/tags/999").status_code == 404
    assert client.delete("/api/tags/groups/999").status_code == 404
    assert client.get("/api/tags").json()[0]["color"] == "#123456"
    assert other["id"] != first["id"]


def test_concurrent_merges_do_not_duplicate_or_lose_associations(client, samples):
    vid = wait_ready(client, upload(client, samples["a"])["id"])["id"]
    client.patch(f"/api/videos/{vid}", json={"tags": ["a", "b", "target"]})
    tags = {tag["name"]: tag["id"] for tag in client.get("/api/tags").json()}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda name: client.post(
                    f"/api/tags/{tags[name]}/merge", json={"target_id": tags["target"]}
                ),
                ["a", "b"],
            )
        )
    assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
    assert client.get(f"/api/videos/{vid}").json()["tags"] == ["target"]


def test_tag_management_requires_authentication(anon):
    for method, path in (
        ("get", "/api/tags"),
        ("get", "/api/tags/groups"),
        ("post", "/api/tags"),
        ("patch", "/api/tags/1"),
        ("delete", "/api/tags/1"),
        ("post", "/api/tags/1/merge"),
        ("post", "/api/tags/groups"),
    ):
        assert getattr(anon, method)(path).status_code == 401


def test_tag_migration_preserves_associations_and_search_on_upgrade_and_downgrade(tmp_path):
    engine = make_engine(tmp_path / "legacy.db")
    cfg = alembic_config(str(engine.url))
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "0018")
        connection.execute(
            text(
                "INSERT INTO videos (id,title,description,original_name,file_path,status,size,"
                "duration,width,height,fps,bitrate,container,video_codec,audio_codec,meta,"
                "has_poster,has_preview,has_sprite,asset_version,cover_time,created_at,rating,"
                "favorite,edit_sources,updated_at) VALUES "
                "('v','title','','file','file','ready',1,1,1,1,"
                "1,1,'mp4','h264','','{}',0,0,0,1,0,CURRENT_TIMESTAMP,0,0,'[]',CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(text("INSERT INTO tags (id,name) VALUES (1,'legacy')"))
        connection.execute(text("INSERT INTO video_tags (video_id,tag_id) VALUES ('v',1)"))
        command.upgrade(cfg, "0019")
        assert connection.execute(text("SELECT count(*) FROM video_tags")).scalar() == 1
        connection.execute(text("INSERT INTO tag_groups (id,name) VALUES (1,'group')"))
        connection.execute(text("UPDATE tags SET color='#abcdef',group_id=1 WHERE id=1"))
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        command.downgrade(cfg, "0018")
        assert connection.execute(text("SELECT name FROM tags WHERE id=1")).scalar() == "legacy"
        assert connection.execute(text("SELECT count(*) FROM video_tags")).scalar() == 1
        assert (
            connection.execute(text("SELECT tags FROM video_search WHERE video_id='v'")).scalar()
            == "legacy"
        )
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()
