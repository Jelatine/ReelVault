from fastapi.testclient import TestClient
from sqlalchemy import select

from reelvault.models import CollectionItem

from .test_organization import seed_video


def test_collection_membership_order_and_independent_folders(client: TestClient) -> None:
    a, b, c = (seed_video(client, title) for title in ("甲", "乙", "丙"))
    folder = client.post("/api/folders", json={"name": "原目录"}).json()["id"]
    client.patch(f"/api/videos/{a}", json={"folder_id": folder, "move": True})
    first = client.post(
        "/api/collections", json={"name": "旅行", "description": "我的播放列表"}
    ).json()
    second = client.post("/api/collections", json={"name": "最爱"}).json()
    url = f"/api/collections/{first['id']}"
    added = client.post(url + "/items", json={"video_ids": [b, a, c, a]})
    assert added.status_code == 200, added.text
    assert [video["id"] for video in added.json()["items"]] == [b, a, c]
    client.post(f"/api/collections/{second['id']}/items", json={"video_ids": [a]})
    assert client.get(f"/api/videos/{a}").json()["folder_id"] == folder
    assert {row["name"]: row["count"] for row in client.get("/api/collections").json()} == {
        "旅行": 3,
        "最爱": 1,
    }
    assert client.post(url + "/items", json={"video_ids": [a, b]}).json()["count"] == 3
    assert client.put(url + "/order", json={"video_ids": [c, b, a]}).status_code == 200
    assert [video["id"] for video in client.get(url).json()["items"]] == [c, b, a]
    renamed = client.put(url, json={"name": "旅行回顾", "description": "按时间排序"}).json()
    assert renamed["description"] == "按时间排序"
    assert client.delete(url + f"/items/{b}").json()["count"] == 2
    assert client.get(f"/api/videos/{b}").status_code == 200
    client.delete(url)
    assert client.get(url).status_code == 404
    assert client.get(f"/api/collections/{second['id']}").json()["count"] == 1
    assert client.get(f"/api/videos/{a}").status_code == 200
    with client.app.state.sessionmaker() as db:
        assert len(db.scalars(select(CollectionItem)).all()) == 1


def test_deleted_members_hidden_restored_and_purge_cascades(client: TestClient) -> None:
    a, b = seed_video(client), seed_video(client)
    collection = client.post("/api/collections", json={"name": "保留关系"}).json()
    url = f"/api/collections/{collection['id']}"
    client.post(url + "/items", json={"video_ids": [a, b]})
    client.delete(f"/api/videos/{a}")
    assert client.get(url).json()["count"] == 1
    assert client.get("/api/collections").json()[0]["count"] == 1
    assert client.put(url + "/order", json={"video_ids": [b]}).status_code == 200
    client.post(f"/api/videos/{a}/restore")
    assert [video["id"] for video in client.get(url).json()["items"]] == [b, a]
    client.delete(f"/api/videos/{a}?permanent=true")
    with client.app.state.sessionmaker() as db:
        assert db.get(CollectionItem, (collection["id"], a)) is None
    assert client.get(url).json()["count"] == 1


def test_validation_and_order_conflicts_do_not_change_collection(client: TestClient) -> None:
    a = seed_video(client)
    created = client.post("/api/collections", json={"name": "校验"})
    assert created.status_code == 200
    url = f"/api/collections/{created.json()['id']}"
    assert client.post("/api/collections", json={"name": " 校验 "}).status_code == 409
    assert client.post("/api/collections", json={"name": "  "}).status_code == 422
    assert client.post(url + "/items", json={"video_ids": [a, "missing"]}).status_code == 404
    assert client.get(url).json()["count"] == 0
    client.post(url + "/items", json={"video_ids": [a]})
    for ids in ([], [a, a], ["missing"]):
        assert client.put(url + "/order", json={"video_ids": ids}).status_code == 409
    assert client.get(url).json()["items"][0]["id"] == a
    assert client.get("/api/collections/99999").status_code == 404


def test_create_with_members_is_atomic(client: TestClient) -> None:
    a = seed_video(client)
    before = client.get("/api/collections").json()
    assert (
        client.post(
            "/api/collections", json={"name": "无效成员", "video_ids": [a, "missing"]}
        ).status_code
        == 404
    )
    assert client.get("/api/collections").json() == before
    result = client.post("/api/collections", json={"name": "直接加入", "video_ids": [a, a]})
    assert result.status_code == 200, result.text
    assert result.json()["count"] == 1
    assert result.json()["items"][0]["id"] == a
