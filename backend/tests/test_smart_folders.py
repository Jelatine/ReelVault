from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from reelvault.auth import hash_password
from reelvault.models import SmartFolder, User

from .conftest import HEADERS, upload_ready


def test_saved_filters_are_dynamic_and_keep_sort_pagination_and_trash_separate(client, samples):
    a = upload_ready(client, samples["a"])
    b = upload_ready(client, samples["b"])
    for item in (a, b):
        client.patch(f"/api/videos/{item['id']}", json={"rating": 4, "tags": ["旅行"]})
    saved = client.post(
        "/api/smart-folders",
        json={
            "name": " 长旅行 ",
            "filters": {
                "rating_min": "4",
                "tag": "旅行",
                "duration_min": "3.5",
                "sort": "title",
                "order": "asc",
            },
        },
    )
    assert saved.status_code == 200, saved.text
    folder = saved.json()
    assert folder["name"] == "长旅行"
    url = f"/api/smart-folders/{folder['id']}/videos"
    assert [v["id"] for v in client.get(url).json()["items"]] == [a["id"]]
    client.patch(f"/api/videos/{a['id']}", json={"rating": 3})
    assert client.get(url).json()["total"] == 0
    client.patch(f"/api/videos/{a['id']}", json={"rating": 5})
    c = upload_ready(client, samples["a"])
    client.patch(f"/api/videos/{c['id']}", json={"rating": 5, "tags": ["旅行"]})
    result = client.get(url, params={"page_size": 1, "page": 2}).json()
    assert result["total"] == 2 and result["page"] == 2 and len(result["items"]) == 1
    client.delete(f"/api/videos/{a['id']}")
    assert client.get(url).json()["total"] == 1
    client.post(f"/api/videos/{a['id']}/restore")
    assert client.get(url).json()["total"] == 2
    assert (
        client.patch(f"/api/smart-folders/{folder['id']}", json={"name": "改名"}).status_code == 200
    )
    assert client.get("/api/smart-folders").json()[0]["filters"] == folder["filters"]
    assert (
        client.patch(
            f"/api/smart-folders/{folder['id']}",
            json={"filters": {"rating_min": 5, "duration_max": 3.1}},
        ).status_code
        == 200
    )
    assert client.get(url).json()["total"] == 0
    assert client.delete(f"/api/smart-folders/{folder['id']}").status_code == 200
    assert client.get(url).status_code == 404
    assert client.get(f"/api/videos/{a['id']}").json()["deleted_at"] is None


def test_saved_tag_filters_follow_rename_merge_and_never_broaden_on_delete(client, samples):
    video = upload_ready(client, samples["a"])
    client.patch(f"/api/videos/{video['id']}", json={"tags": ["旧名"]})
    tags = client.get("/api/tags").json()
    old = tags[0]
    target = client.post("/api/tags", json={"name": "目标"}).json()
    saved = client.post(
        "/api/smart-folders", json={"name": "人物", "filters": {"tag": "旧名"}}
    ).json()
    url = f"/api/smart-folders/{saved['id']}/videos"
    assert client.patch(f"/api/tags/{old['id']}", json={"name": "新名"}).status_code == 200
    assert client.get("/api/smart-folders").json()[0]["filters"]["tag"] == "新名"
    assert client.get(url).json()["total"] == 1
    assert (
        client.post(f"/api/tags/{old['id']}/merge", json={"target_id": target["id"]}).status_code
        == 200
    )
    assert client.get("/api/smart-folders").json()[0]["filters"]["tag"] == "目标"
    assert client.get(url).json()["total"] == 1
    client.delete(f"/api/tags/{target['id']}")
    assert client.get("/api/smart-folders").json()[0]["filters"]["tag"] == "目标"
    assert client.get(url).json()["total"] == 0


def test_all_saved_conditions_share_the_regular_video_evaluator(client, samples):
    root = client.post("/api/folders", json={"name": "根"}).json()
    child = client.post("/api/folders", json={"name": "子", "parent_id": root["id"]}).json()
    video = upload_ready(client, samples["a"], folder_id=child["id"])
    client.patch(
        f"/api/videos/{video['id']}",
        json={
            "title": "旅行素材",
            "rating": 5,
            "favorite": True,
            "tags": ["地点"],
        },
    )
    filters = {
        "q": "旅行",
        "folder": str(root["id"]),
        "include_children": True,
        "tag": "地点",
        "rating_min": 4,
        "favorite": True,
        "duration_min": 3,
        "duration_max": 5,
        "size_min": 1,
        "size_max": 10000000,
        "resolution": "landscape",
        "codec": "h264",
        "format": "mp4",
        "created_after": "2020-01-01T00:00:00Z",
        "created_before": "2030-01-01T00:00:00Z",
        "sort": "title",
        "order": "asc",
    }
    # capture date is metadata-derived; set it through the model for this fixture.
    with client.app.state.sessionmaker() as db:
        from reelvault.models import Video

        db.get(Video, video["id"]).captured_at = datetime(2024, 1, 2, tzinfo=UTC)
        db.commit()
    filters.update(
        captured_after="2024-01-01T12:00:00+12:00", captured_before="2024-01-03T00:00:00Z"
    )
    saved = client.post("/api/smart-folders", json={"name": "复合条件", "filters": filters})
    assert saved.status_code == 200, saved.text
    expected = client.get(
        "/api/videos",
        params={k: str(v).lower() if isinstance(v, bool) else v for k, v in filters.items()},
    ).json()
    actual = client.get(f"/api/smart-folders/{saved.json()['id']}/videos").json()
    assert actual == expected and actual["total"] == 1
    client.delete(f"/api/folders/{child['id']}")
    assert client.get(f"/api/smart-folders/{saved.json()['id']}/videos").json()["total"] == 1
    client.delete(f"/api/folders/{root['id']}")
    assert client.get(f"/api/smart-folders/{saved.json()['id']}/videos").json()["total"] == 0


def test_smart_folders_are_private_and_require_login(client):
    saved = client.post("/api/smart-folders", json={"name": "私人", "filters": {}}).json()
    with client.app.state.sessionmaker() as db:
        db.add(User(username="other", password_hash=hash_password("secret456")))
        db.commit()
    other = TestClient(client.app, headers=HEADERS)
    assert (
        other.post(
            "/api/auth/login", json={"username": "other", "password": "secret456"}
        ).status_code
        == 200
    )
    assert other.get("/api/smart-folders").json() == []
    for method, path, body in (
        ("get", f"/{saved['id']}/videos", None),
        ("patch", f"/{saved['id']}", {"name": "窃取"}),
        ("delete", f"/{saved['id']}", None),
    ):
        assert other.request(method, "/api/smart-folders" + path, json=body).status_code == 404
    assert other.post("/api/smart-folders", json={"name": "私人", "filters": {}}).status_code == 200
    other.cookies.clear()
    assert other.get("/api/smart-folders").status_code == 401
    assert other.post("/api/smart-folders", json={"name": "新建", "filters": {}}).status_code == 401
    assert other.get(f"/api/smart-folders/{saved['id']}/videos").status_code == 401


def test_invalid_conditions_conflicts_and_concurrent_creation(client):
    saved = client.post(
        "/api/smart-folders", json={"name": "唯一", "filters": {"favorite": False}}
    ).json()
    other = client.post("/api/smart-folders", json={"name": "另一", "filters": {}}).json()
    conflict = client.patch(
        f"/api/smart-folders/{other['id']}", json={"name": "唯一", "filters": {"rating_min": 5}}
    )
    assert conflict.status_code == 409 and conflict.json()["code"] == "smart_folder_name_conflict"
    assert (
        next(f for f in client.get("/api/smart-folders").json() if f["id"] == other["id"])[
            "filters"
        ]["rating_min"]
        == 0
    )
    for filters in (
        {"duration_min": -1},
        {"duration_min": "nan"},
        {"size_min": -1},
        {"size_min": 2**63},
        {"rating_min": 6},
        {"folder": "bad"},
        {"folder": "0"},
        {"folder": str(2**63)},
        {"folder": "9" * 100},
        {"trash": True},
        {"page": 2},
        {"sort": "bad"},
        {"resolution": "bad"},
        {"created_after": "bad"},
        {"favorite": "bad"},
    ):
        assert (
            client.post("/api/smart-folders", json={"name": "无效", "filters": filters}).status_code
            == 422
        )
    assert client.get(f"/api/smart-folders/{2**63}/videos").status_code == 422
    for filters in (
        {"duration_min": 3, "duration_max": 2},
        {"size_min": 2, "size_max": 1},
        {"captured_after": "2025-01-01", "captured_before": "2024-01-01"},
    ):
        assert (
            client.post(
                "/api/smart-folders", json={"name": "无效范围", "filters": filters}
            ).status_code
            == 400
        )
    for body in ({"name": None}, {"name": "  "}, {"filters": None}, {"name": "x" * 129}):
        assert client.patch(f"/api/smart-folders/{saved['id']}", json=body).status_code == 422
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: (
                    client.post(
                        "/api/smart-folders", json={"name": "并发", "filters": {}}
                    ).status_code
                ),
                range(2),
            )
        )
    assert sorted(results) == [200, 409]


def test_smart_folders_survive_database_snapshot_and_migration_downgrade(
    client, settings, tmp_path
):
    from alembic import command

    from reelvault.backup import create_backup, snapshot
    from reelvault.migrate import alembic_config

    saved = client.post(
        "/api/smart-folders", json={"name": "备份", "filters": {"tag": "旅行"}}
    ).json()
    archive = create_backup(settings, tmp_path / "backup.zip")
    import zipfile

    with zipfile.ZipFile(archive) as zipped:
        zipped.extract("reelvault.db", tmp_path / "backup")
    with sqlite3.connect(tmp_path / "backup/reelvault.db") as db:
        assert db.execute("SELECT name FROM smart_folders").fetchall() == [("备份",)]
    copy = tmp_path / "old.db"
    snapshot(settings.db_path, copy)
    cfg = alembic_config(f"sqlite:///{copy}")
    command.downgrade(cfg, "0019")
    with sqlite3.connect(copy) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0019",)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    command.upgrade(cfg, "head")
    with client.app.state.sessionmaker() as db:
        assert db.scalar(select(SmartFolder).where(SmartFolder.id == saved["id"])).name == "备份"
