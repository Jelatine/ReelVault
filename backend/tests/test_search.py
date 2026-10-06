from datetime import UTC, datetime

from fastapi.testclient import TestClient

from reelvault.models import Tag, Video

from .test_organization import seed_video


def result_ids(client: TestClient, **params: str | int | bool) -> list[str]:
    response = client.get("/api/videos", params=params)
    assert response.status_code == 200, response.text
    return [video["id"] for video in response.json()["items"]]


def test_full_text_fields_ranking_short_chinese_and_index_updates(client: TestClient) -> None:
    title = seed_video(client, "旅行日落风景")
    desc = seed_video(client, "普通标题")
    original = seed_video(client, "第三部")
    tagged = seed_video(client, "第四部")
    client.patch(f"/api/videos/{desc}", json={"description": "这是一段旅行日落风景的素材"})
    client.patch(f"/api/videos/{tagged}", json={"tags": ["旅行日落风景", "海"]})
    with client.app.state.sessionmaker() as db:
        db.get(Video, original).original_name = "旅行日落风景.mp4"
        db.commit()
    ids = result_ids(client, q="旅行日落")
    assert set(ids) == {title, desc, original, tagged}
    assert ids[0] == title
    page = client.get("/api/videos", params={"q": "旅行日落", "page_size": 1}).json()
    assert page["total"] == 4 and len(page["items"]) == 1
    assert "旅行日落" in page["items"][0]["search_excerpt"]
    assert result_ids(client, q="旅行")[0] == title
    assert result_ids(client, q="海") == [tagged]
    assert result_ids(client, q="旅行日落 海") == [tagged]
    assert result_ids(client, q="不存在") == []
    for literal in ('"', "OR", "*", "_", "%", "NEAR(foo bar)", "a' OR 1=1 --"):
        result_ids(client, q=literal)  # literals never interpreted as SQL/FTS syntax
    client.patch(f"/api/videos/{title}", json={"title": "更新后"})
    assert title not in result_ids(client, q="旅行日落")
    client.patch(f"/api/videos/{tagged}", json={"tags": ["新标签"]})
    assert tagged not in result_ids(client, q="旅行日落")
    with client.app.state.sessionmaker() as db:
        tag = db.query(Tag).filter(Tag.name == "新标签").one()
        tag.name = "重命名标签"
        db.commit()
    assert result_ids(client, q="重命名") == [tagged]
    client.delete(f"/api/videos/{desc}")
    assert desc not in result_ids(client, q="旅行日落")
    assert result_ids(client, q="旅行日落", trash=True) == [desc]
    client.delete(f"/api/videos/{desc}?permanent=true")
    assert result_ids(client, q="旅行日落", trash=True) == []


def test_advanced_filters_intersect_and_include_descendants(client: TestClient) -> None:
    root = client.post("/api/folders", json={"name": "父目录"}).json()["id"]
    child = client.post("/api/folders", json={"name": "子目录", "parent_id": root}).json()["id"]
    grandchild = client.post("/api/folders", json={"name": "孙目录", "parent_id": child}).json()[
        "id"
    ]
    target, other = seed_video(client), seed_video(client)
    with client.app.state.sessionmaker() as db:
        video = db.get(Video, target)
        video.width, video.height = 2160, 3840
        video.size, video.duration = 4096, 150
        video.video_codec, video.container = "hevc", "mkv"
        video.rating, video.favorite, video.folder_id = 5, True, grandchild
        video.captured_at = datetime(2024, 5, 20, tzinfo=UTC)
        video.created_at = datetime(2026, 9, 1, tzinfo=UTC)
        db.commit()
    assert result_ids(client, folder=root) == []
    assert result_ids(client, folder=root, include_children=True) == [target]
    assert result_ids(client, resolution="portrait") == [target]
    assert result_ids(client, resolution="4k") == [target]
    assert result_ids(client, codec="hevc", format="mkv", rating_min=4, favorite=True) == [target]
    assert result_ids(client, duration_min=130, duration_max=160, size_min=4000, size_max=5000) == [
        target
    ]
    assert result_ids(
        client, captured_after="2024-05-01", captured_before="2024-05-31T23:59:59Z"
    ) == [target]
    assert result_ids(client, created_before="2026-09-02") == [target]
    assert result_ids(client, duration_max=130, size_max=1000) == [other]
    assert result_ids(client, codec="hevc", favorite=False) == []
    for params in (
        {"duration_min": 200, "duration_max": 100},
        {"size_min": 20, "size_max": 10},
        {"created_after": "2026-10-01", "created_before": "2026-09-01T00:00:00Z"},
    ):
        assert client.get("/api/videos", params=params).status_code == 400


def test_numeric_filters_reject_non_finite_values(client: TestClient) -> None:
    for value in ("NaN", "Infinity", "-Infinity"):
        assert client.get("/api/videos", params={"duration_min": value}).status_code == 422
